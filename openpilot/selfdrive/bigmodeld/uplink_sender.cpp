#include "uplink_sender.h"

#include <cstring>

namespace {

inline void put_u32_le(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)v;
  p[1] = (uint8_t)(v >> 8);
  p[2] = (uint8_t)(v >> 16);
  p[3] = (uint8_t)(v >> 24);
}

}  // namespace

UplinkSender::UplinkSender(UplinkSocket* sock, std::function<uint64_t()> now_ms,
                           UplinkSenderConfig cfg, EventFn on_event)
    : sock_(sock), now_(std::move(now_ms)), cfg_(cfg), on_event_(std::move(on_event)) {}

void UplinkSender::emit_locked(UplinkEvent ev, uint32_t frame_idx) {
  if (on_event_) on_event_(UplinkEventInfo{ev, frame_idx});
}

UplinkSender::OutFrame* UplinkSender::find_or_create_locked(uint32_t frame_idx) {
  if (has_inflight_ && inflight_.frame_idx == frame_idx) return &inflight_;
  if (has_queued_ && queued_.frame_idx == frame_idx) return &queued_;
  // 迟到的旧包（该帧已被覆盖丢弃并上报过）：静默丢弃
  if (has_inflight_ && frame_idx < inflight_.frame_idx) return nullptr;
  if (has_queued_ && frame_idx < queued_.frame_idx) return nullptr;
  // 新包覆盖排队槽 = 丢弃旧包（丢帧）
  if (has_queued_) {
    emit_locked(UplinkEvent::kDrop, queued_.frame_idx);
    has_queued_ = false;
  }
  queued_ = OutFrame{};
  queued_.frame_idx = frame_idx;
  has_queued_ = true;
  return &queued_;
}

void UplinkSender::submit_road(uint32_t frame_idx, const bgm1::FrameHeader& hdr,
                               const uint8_t* road, size_t road_len,
                               bool road_idr_actual, bool wide_idr_predicted) {
  std::lock_guard<std::mutex> lk(mtx_);
  if (!connected_) return;  // 断连期间的帧静默丢弃（重连即新序列，frame_idx 归零）

  OutFrame* f = find_or_create_locked(frame_idx);
  if (f == nullptr || f->has_road) return;

  bgm1::FrameHeader h = hdr;
  h.frame_idx = frame_idx;
  h.road_len = (uint32_t)road_len;
  h.wide_len = 0;  // 线上 wide_len 在 chunk2（10 号布局：头里 len 只表示 road 段）
  h.flags = (uint16_t)((road_idr_actual ? bgm1::kFlagRoadIdr : 0) |
                       (wide_idr_predicted ? bgm1::kFlagWideIdr : 0));

  f->chunk1.resize(bgm1::kFrameHdrSize + road_len);
  bgm1::pack_frame_header(h, f->chunk1.data());
  std::memcpy(f->chunk1.data() + bgm1::kFrameHdrSize, road, road_len);
  f->has_road = true;
  f->wide_idr_predicted = wide_idr_predicted;
}

void UplinkSender::submit_wide(uint32_t frame_idx, const uint8_t* wide, size_t wide_len,
                               bool wide_actual_idr) {
  std::lock_guard<std::mutex> lk(mtx_);
  if (!connected_) return;

  OutFrame* f = find_or_create_locked(frame_idx);
  if (f == nullptr || f->has_wide) return;
  if (wide_actual_idr != f->wide_idr_predicted) {
    // bit1 策略预测与实际不符：协议上只允许欠报（预测 0 实际 IDR 无害），
    // 误报（预测 1 实际 P 帧）会让服务端判 BAD_FRAME——真机验证「I 帧位置符合策略」兜底
    wide_idr_mismatches_++;
  }

  f->chunk2.resize(sizeof(uint32_t) + wide_len + bgm1::kMacSize);
  put_u32_le(f->chunk2.data(), (uint32_t)wide_len);
  std::memcpy(f->chunk2.data() + sizeof(uint32_t), wide, wide_len);
  std::memset(f->chunk2.data() + sizeof(uint32_t) + wide_len, 0, bgm1::kMacSize);
  f->has_wide = true;
}

void UplinkSender::drop_connection_locked() {
  sock_->close();
  connected_ = false;
  // 旧帧不得带进新连接（重连即新序列、frame_idx 归零）：静默清空
  has_inflight_ = false;
  has_queued_ = false;
  inflight_ = OutFrame{};
  queued_ = OutFrame{};
}

void UplinkSender::try_finish_inflight_locked() {
  if (!has_inflight_) return;
  // 写得动就发完（含连续部分写，不撕裂），写不动/写错才截断（drop_connection_locked 清残帧）
  if (write_out_locked(inflight_, 0, false) == WriteState::kDone) {
    uint32_t idx = inflight_.frame_idx;
    has_inflight_ = false;
    emit_locked(UplinkEvent::kFrameSent, idx);
  }
}

UplinkSender::WriteState UplinkSender::write_out_locked(OutFrame& f, uint64_t now, bool track_progress) {
  while (true) {
    if (!f.chunk1_done) {
      int n = sock_->write_some(f.chunk1.data() + f.sent1, f.chunk1.size() - f.sent1);
      if (n < 0) return WriteState::kError;
      if (n == 0) return WriteState::kBlocked;
      f.sent1 += (size_t)n;
      if (track_progress) last_progress_ms_ = now;
      if (f.sent1 < f.chunk1.size()) continue;  // 部分写继续推进
      f.chunk1_done = true;
    }
    if (!f.has_wide) return WriteState::kBlocked;  // 等 wide 包（road 出包即发：chunk1 已在路上）
    int n = sock_->write_some(f.chunk2.data() + f.sent2, f.chunk2.size() - f.sent2);
    if (n < 0) return WriteState::kError;
    if (n == 0) return WriteState::kBlocked;
    f.sent2 += (size_t)n;
    if (track_progress) last_progress_ms_ = now;
    if (f.sent2 < f.chunk2.size()) continue;
    return WriteState::kDone;
  }
}

bool UplinkSender::step() {
  uint64_t now = now_();

  {
    std::lock_guard<std::mutex> lk(mtx_);
    if (connected_) {
      bool progress = false;

      // 排队槽转在途（只有 road 段到齐才起发——road 出包即发）
      if (!has_inflight_ && has_queued_ && queued_.has_road) {
        inflight_ = std::move(queued_);
        has_queued_ = false;
        has_inflight_ = true;
        last_progress_ms_ = now;  // 新在途帧起点，空闲期不计入假死
        progress = true;
      }
      if (!has_inflight_) return progress;

      const uint32_t idx = inflight_.frame_idx;
      const size_t before = inflight_.sent1 + inflight_.sent2;
      WriteState ws = write_out_locked(inflight_, now, true);
      if (ws == WriteState::kDone) {
        has_inflight_ = false;
        emit_locked(UplinkEvent::kFrameSent, idx);
        return true;
      }
      if (ws == WriteState::kError) {
        drop_connection_locked();
        return progress;
      }
      progress |= (inflight_.sent1 + inflight_.sent2) != before;

      // 假死 = 对在途帧 ≥200 ms 无进展（写阻塞或 wide 缺失）：
      // 发完在途帧（写得动就发完）或截断，随后重连，上报重置
      if (now - last_progress_ms_ >= cfg_.stall_ms) {
        emit_locked(UplinkEvent::kStall, idx);
        try_finish_inflight_locked();
        drop_connection_locked();
      }
      return progress;
    }
  }

  // 未连接：建连在锁外做（最长 connect_timeout_ms），submit_* 走「未连接即丢」快路径
  bool ok = sock_->connect(cfg_.connect_timeout_ms);
  std::lock_guard<std::mutex> lk(mtx_);
  if (ok) {
    connected_ = true;
    connect_failures_ = 0;
    last_progress_ms_ = now_();
    emit_locked(UplinkEvent::kNewConnection, 0);
    return true;
  }
  if (++connect_failures_ == cfg_.max_connect_failures) {
    emit_locked(UplinkEvent::kLinkLost, 0);
  }
  return false;
}

void UplinkSender::notify_disconnect() {
  std::lock_guard<std::mutex> lk(mtx_);
  if (connected_) drop_connection_locked();
}

bool UplinkSender::connected() const {
  std::lock_guard<std::mutex> lk(mtx_);
  return connected_;
}

int UplinkSender::connect_failures() const {
  std::lock_guard<std::mutex> lk(mtx_);
  return connect_failures_;
}

uint64_t UplinkSender::wide_idr_mismatches() const {
  std::lock_guard<std::mutex> lk(mtx_);
  return wide_idr_mismatches_;
}

// ---- ReplyTracker ----

const char* ReplyTracker::segment_name(int i) {
  static const char* kNames[kSegments] = {"srv_recv_us", "srv_prep_us", "srv_htp_us", "srv_total_us"};
  return (i >= 0 && i < kSegments) ? kNames[i] : "?";
}

void ReplyTracker::on_reply(const bgm1::Reply& r) {
  latest_ = r;
  has_reply_ = true;
  replies_++;
  if (r.seq_reset()) seq_reset_++;
  if (r.zero_pair()) zero_pair_++;
  for (int i = 0; i < kSegments; i++) segments_[i].add(r.telemetry[i]);
}

void ReplyTracker::on_err(const bgm1::ErrMsg& e) {
  last_err_ = e;
  has_err_ = true;
}

void ReplyTracker::on_disconnect() {
  // 断连只清「最新缓存」的时效性，统计保留（分段遥测跨连接累计）
}
