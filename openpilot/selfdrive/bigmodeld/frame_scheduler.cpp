#include "frame_scheduler.h"

SchedStep FrameScheduler::start_new_sequence(bool drop_detected) {
  SchedStep s;
  s.drop_detected = drop_detected;
  s.request_keyframe_road = true;
  s.request_keyframe_wide = true;
  pending_req_road_ = true;
  pending_req_wide_ = true;
  seq_pos_ = 0;
  next_idr_seq_road_ = 0;
  next_idr_seq_wide_ = 0;
  // 显式上报与空洞检测互斥：新序列起点未「发出」过，后续空洞检测从下一次 on_frame_sent 起算
  have_last_sent_ = false;
  return s;
}

SchedStep FrameScheduler::on_connect() {
  SchedStep s = start_new_sequence(false);
  have_last_submit_ = false;
  // frame_idx 重新编号，去重窗口作废
  recent_drop_n_ = 0;
  recent_drop_pos_ = 0;
  return s;
}

SchedStep FrameScheduler::on_frame_submit(uint32_t frame_idx) {
  SchedStep s;

  // 提交序缺口兜底：帧在进编码器前死亡但没人显式上报（如两路相机同时缺帧）
  if (have_last_submit_ && frame_idx != last_submit_idx_ + 1) {
    SchedStep d = start_new_sequence(true);
    s.request_keyframe_road = s.request_keyframe_road || d.request_keyframe_road;
    s.request_keyframe_wide = s.request_keyframe_wide || d.request_keyframe_wide;
    s.drop_detected = true;
  }
  last_submit_idx_ = frame_idx;
  have_last_submit_ = true;

  // 新序列第 10 帧给 wide 再补一次 I 帧（恢复「wide 比 road 晚 10 帧」相位）
  if (seq_pos_ == 10) {
    s.request_keyframe_wide = true;
    pending_req_wide_ = true;
  }

  // 本帧 IDR 预测 = request 落点（request 先于本帧 encode_frame，21 号「下一帧立即 I 帧」）
  // ∪ GOP 20 基准（上一个 IDR 后第 20 帧）。预测即 FRAME 头 flags 语义。
  s.road_idr = pending_req_road_ || (seq_pos_ == next_idr_seq_road_);
  if (s.road_idr) {
    pending_req_road_ = false;
    next_idr_seq_road_ = seq_pos_ + 20;
  }
  s.wide_idr = pending_req_wide_ || (seq_pos_ == next_idr_seq_wide_);
  if (s.wide_idr) {
    pending_req_wide_ = false;
    next_idr_seq_wide_ = seq_pos_ + 20;
  }

  seq_pos_++;
  return s;
}

// 同一 frame 的重复显式上报只报一次：查窗口并在窗口内登记（新报才登记）。
bool FrameScheduler::drop_already_reported(uint32_t frame_idx) {
  for (int i = 0; i < recent_drop_n_; i++) {
    if (recent_drop_[i] == frame_idx) return true;
  }
  recent_drop_[recent_drop_pos_] = frame_idx;
  recent_drop_pos_ = (recent_drop_pos_ + 1) % kDropDedupWindow;
  if (recent_drop_n_ < kDropDedupWindow) recent_drop_n_++;
  return false;
}

SchedStep FrameScheduler::on_frame_dropped(uint32_t frame_idx) {
  // 配对缺帧是「每侧各杀一次」的：同一 frame_id 的第二次显式上报静默，
  // 不再开新序列/重复 request（否则会多插一对 IDR 并双计丢帧）。
  if (drop_already_reported(frame_idx)) return SchedStep{};

  SchedStep s = start_new_sequence(true);
  // 显式上报后提交序锚点前移：下一个提交帧不把同一死亡再报成空洞（互斥同
  // have_last_sent_ 口径）；其后真正缺帧仍会以空洞暴露。锚点只前进不后退——
  // 发送队列覆盖丢弃的帧已提交过，锚点可能在其后。
  if (!have_last_submit_ || frame_idx > last_submit_idx_) {
    last_submit_idx_ = frame_idx;
    have_last_submit_ = true;
  }
  return s;
}

SchedStep FrameScheduler::on_frame_sent(uint32_t frame_idx) {
  if (!have_last_sent_) {
    // 新序列的第一发出帧（显式丢弃上报后或连接刚建立）
    have_last_sent_ = true;
    last_sent_idx_ = frame_idx;
    return SchedStep{};
  }
  if (frame_idx != last_sent_idx_ + 1) {
    // 实际发出 frame_idx 序列出现空洞 = 丢帧
    last_sent_idx_ = frame_idx;
    return start_new_sequence(true);
  }
  last_sent_idx_ = frame_idx;
  return SchedStep{};
}
