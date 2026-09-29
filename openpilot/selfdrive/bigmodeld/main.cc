// bigmodeld：C4 上行进程（16 号）。生命周期跟随 modeld（process_config only_onroad）：
//   camered VisionIPC 两路取帧 → 编码缓冲拷贝 → 按 frame_id 配对 → V4L 双路硬编
//   （HEVC Main、VBR、无 B 帧、GOP 20、默认 10 Mb/s/路可调）→ 10 号布局组 FRAME
//   经 TCP 发出（road 出包即发）；REPLY 独立线程收下解析记账。不落盘、不进 loggerd
//   （设了编码输出回调 ⇒ 不建 PubMaster ⇒ loggerd 无编码数据）。
//
// 实现口径见本目录 README「实现口径」；纯逻辑在 frame_scheduler / uplink_sender /
// frame_meta / frame_codec，宿主单测 test_bigmodeld.cc。
//
// 线程与锁序（全文件统一，防死锁）：
//   取帧×2（state_mtx：配对/状态机/索引 + 编码缓冲池）、编码器 dequeue×2（输出回调：
//   meta 缓存 → sender.submit_*）、writer（sender.step）、REPLY reader（read_some →
//   parse → ReplyTracker）、标定/输入元数据（SubMaster → MetaProvider.set_rpy/set_model_inputs）。
//   锁序：sender.mtx > state_mtx > {ev_mtx, meta 缓存, buf 池}（叶子锁，绝不反向嵌套）。
//   sender 事件回调在其锁内触发 ⇒ 回调只记账 + 推事件队列（ev_mtx），状态机转移与
//   request_keyframe 在下一次组帧前由 drain_events_locked() 按序执行（事件仍落在
//   「事件后的第一个提交帧」，且不会在回调里反向取 state_mtx）。

#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "common/params.h"
#include "common/timing.h"
#include "common/util.h"
#include "cereal/messaging/messaging.h"
#include "system/loggerd/encoder/v4l_encoder.h"
#include "system/loggerd/loggerd.h"

#include "frame_codec.h"
#include "frame_meta.h"
#include "frame_scheduler.h"
#include "meta_cache.h"
#include "pair_matcher.h"
#include "uplink_sender.h"

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

ExitHandler do_exit;

namespace {

constexpr int kFps = 20;
constexpr int kGopSize = 20;
constexpr int kDefaultBitrate = 10'000'000;  // 10 Mb/s/路（票面默认）
// QBUF 要求 plane.length ≥ S_FMT sizeimage（1344×760 NV12 = 2,428,928 B，21 号 EINVAL 坑）
constexpr size_t kNv12SizeImage = 2428928;
constexpr int kEncPoolDepth = 6;   // 每路编码缓冲池（1 配对槽 + ≤5 在编码器在途）
constexpr int kSchedFifoPrio = 53;
const std::vector<int> kCpuAffinity = {3};  // 仿 encoderd.cc:216；EINVAL（离线核）容忍

enum StreamId { kRoad = 0, kWide = 1 };

void setup_realtime(const char* who) {
  if (util::set_realtime_priority(kSchedFifoPrio) != 0) {
    LOGW("bigmodeld: %s SCHED_FIFO %d 失败 errno=%d", who, kSchedFifoPrio, errno);
  }
  if (util::set_core_affinity(kCpuAffinity) != 0) {
    LOGW("bigmodeld: %s 绑核失败（容忍）errno=%d", who, errno);
  }
}

// ---- TCP 上行 socket（可注入接缝的系统实现）----
// 非阻塞：write_some/read_some 语义对齐 UplinkSocket（>0 进展 / 0 会阻塞 / -1 错或 EOF）。
class TcpSocket : public UplinkSocket {
 public:
  TcpSocket(std::string host, int port) : host_(std::move(host)), port_(port) {}
  ~TcpSocket() override { close(); }

  bool connect(int timeout_ms) override {
    std::lock_guard<std::mutex> lk(mtx_);
    close_locked();

    struct addrinfo hints = {}, *res = nullptr;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char portstr[16];
    std::snprintf(portstr, sizeof portstr, "%d", port_);
    if (getaddrinfo(host_.c_str(), portstr, &hints, &res) != 0 || res == nullptr) return false;

    int fd = ::socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd < 0) {
      freeaddrinfo(res);
      return false;
    }
    int fl = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);

    int rc = ::connect(fd, res->ai_addr, res->ai_addrlen);
    freeaddrinfo(res);
    if (rc != 0) {
      if (errno != EINPROGRESS) {
        ::close(fd);
        return false;
      }
      struct pollfd pfd = {.fd = fd, .events = POLLOUT, .revents = 0};
      if (poll(&pfd, 1, timeout_ms) <= 0) {
        ::close(fd);
        return false;
      }
      int err = 0;
      socklen_t el = sizeof err;
      if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el) != 0 || err != 0) {
        ::close(fd);
        return false;
      }
    }
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
    fd_ = fd;
    return true;
  }

  int write_some(const uint8_t* data, size_t len) override {
    std::lock_guard<std::mutex> lk(mtx_);
    if (fd_ < 0) return -1;
    ssize_t n = ::send(fd_, data, len, MSG_NOSIGNAL);
    if (n > 0) return (int)n;
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return 0;
    return -1;
  }

  int read_some(uint8_t* data, size_t len) override {
    std::lock_guard<std::mutex> lk(mtx_);
    if (fd_ < 0) return -1;
    ssize_t n = ::recv(fd_, data, len, 0);
    if (n > 0) return (int)n;
    if (n == 0) return -1;  // EOF = 断连
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
    return -1;
  }

  void close() override {
    std::lock_guard<std::mutex> lk(mtx_);
    close_locked();
  }

 private:
  void close_locked() {
    if (fd_ >= 0) {
      ::close(fd_);
      fd_ = -1;
    }
  }

  std::mutex mtx_;
  int fd_ = -1;
  std::string host_;
  int port_;
};

// ---- 编码缓冲池：VisionIPC 帧拷进 plane.length ≥ sizeimage 的自有缓冲（21 号坑）----
// encode_frame 把缓冲排队进 V4L（最多 BUF_IN_COUNT 在途），编码器 dequeue 线程经
// input_done_callback 归还。acquire 阻塞 = 上游背压。
class BufPool {
 public:
  void init(size_t len, size_t width, size_t height, size_t stride, size_t uv_offset) {
    std::lock_guard<std::mutex> lk(mtx_);
    len_ = len;
    bufs_.resize(kEncPoolDepth);
    for (auto& b : bufs_) {
      b.allocate(len);
      b.init_yuv(width, height, stride, uv_offset);
      free_.push_back(&b);
    }
  }

  ~BufPool() {
    for (auto& b : bufs_) b.free();
  }

  VisionBuf* acquire() {
    std::unique_lock<std::mutex> lk(mtx_);
    cv_.wait(lk, [this] { return !free_.empty(); });
    VisionBuf* b = free_.back();
    free_.pop_back();
    return b;
  }

  void release(VisionBuf* b) {
    {
      std::lock_guard<std::mutex> lk(mtx_);
      free_.push_back(b);
    }
    cv_.notify_one();
  }

  size_t len() const { return len_; }

 private:
  std::mutex mtx_;
  std::condition_variable cv_;
  std::vector<VisionBuf> bufs_;
  std::vector<VisionBuf*> free_;
  size_t len_ = 0;
};

// ---- 组帧提交上下文：meta_cache.h（查表 + miss 分类，宿主单测）----

struct EncoderCtx {
  BufPool pool;
  MetaCache meta;
  std::unique_ptr<V4LEncoder> enc;
  std::vector<uint8_t> au;  // 回调线程私有（每编码器一条 dequeue 线程）
};

// ---- 主状态 ----
class Bigmodeld {
 public:
  Bigmodeld(std::string host, int port, int bitrate)
      : sock_(std::move(host), port), bitrate_(bitrate), cur_bitrate_(bitrate) {
    sender_ = std::make_unique<UplinkSender>(
        &sock_, [] { return (uint64_t)millis_since_boot(); }, UplinkSenderConfig{},
        [this](const UplinkEventInfo& e) { on_uplink_event(e); });
  }

  // ===== 取帧/配对线程（×2）=====
  void capture_thread(StreamId sid) {
    setup_realtime(sid == kRoad ? "bgm_road" : "bgm_wide");

    VisionStreamType type = sid == kRoad ? VISION_STREAM_NARROW_ROAD : VISION_STREAM_WIDE_ROAD;
    VisionIpcClient vipc("camerad", type, false);
    bool inited = false;

    while (!do_exit) {
      if (!vipc.connect(false)) {
        util::sleep_for(100);
        continue;
      }
      if (!inited) {
        init_encoder(sid, vipc.buffers[0]);
        inited = true;
      }
      while (!do_exit) {
        VisionIpcBufExtra extra;
        VisionBuf* buf = vipc.recv(&extra, 100);
        if (buf == nullptr) continue;
        // 上游覆盖/滞后（缓冲被新帧顶掉）：不进编码器；后续 SOF 可形成时间槽空洞
        if (buf->get_frame_id() != extra.frame_id) continue;

        // 拷进编码缓冲后立即可放回 VisionIPC 缓冲（配对等待不占上游缓冲）
        VisionBuf* dst = ctx_[sid].pool.acquire();
        std::memcpy(dst->addr, buf->addr, std::min(buf->len, ctx_[sid].pool.len()));
        place_frame(sid, extra, dst);
      }
    }
  }

  // ===== 发送线程 =====
  void writer_thread() {
    setup_realtime("bgm_writer");
    uint64_t report_k = 0;
    while (!do_exit) {
      if (!sender_->step()) util::sleep_for(2);
      // 发送段耗时报表（17 号）：每 1000 帧一行；样本 = 在途起点 → 整帧写完。
      // 统计放这里 = sender 锁外（事件回调只记账+入队，见 on_uplink_event 头注）
      const uint64_t k = sender_->frames_sent() / 1000;
      if (k > report_k) {
        report_k = k;
        std::vector<double> samples = sender_->send_samples();
        std::sort(samples.begin(), samples.end());
        auto pct = [&](double p) {
          return samples.empty() ? 0.0 : samples[std::min(samples.size() - 1, (size_t)(samples.size() * p))];
        };
        LOGE("bigmodeld: 发送段 n=%zu p50=%.2f p90=%.2f p99=%.2f max=%.2f ms",
             samples.size(), pct(0.5), pct(0.9), pct(0.99), samples.empty() ? 0.0 : samples.back());
      }
    }
  }

  // ===== REPLY 接收线程（(e)）=====
  void reply_thread() {
    setup_realtime("bgm_reply");
    std::vector<uint8_t> buf;

    // 坏流/EOF/ERR 统一收尾：记断连 + 走重连路径 + 清半包缓冲（16 号 code-review：Duplicated Code）
    auto fail = [&](const char* why) {
      LOGE("bigmodeld: REPLY 流异常（%s），断连重连", why);
      tracker_.on_disconnect();
      sender_->notify_disconnect();
      buf.clear();
    };

    while (!do_exit) {
      if (!sender_->connected()) {
        util::sleep_for(50);
        continue;
      }
      uint8_t tmp[8192];
      int n = sock_.read_some(tmp, sizeof tmp);
      if (n < 0) {
        // EOF/错误：走重连路径；统计保留（分段遥测跨连接累计）
        fail("EOF/错误");
        continue;
      }
      if (n == 0) {
        util::sleep_for(2);
        continue;
      }
      buf.insert(buf.end(), tmp, tmp + n);

      // MsgHdr 分流：REPLY/ERR 定长整条解析，坏流即断连重来
      while (buf.size() >= bgm1::kMsgHdrSize) {
        const uint8_t type = buf[5];
        const uint32_t len = bgm1::read_u32_le(buf.data() + 12);
        size_t need = 0;
        if (type == bgm1::kTypeReply && len == bgm1::kReplyPayloadSize) {
          need = bgm1::kReplyWireSize;
        } else if (type == bgm1::kTypeErr && len == bgm1::kErrPayloadSize) {
          need = bgm1::kErrWireSize;
        } else {
          LOGE("bigmodeld: REPLY 流协议错 type=0x%02x len=%u", type, len);
          fail("协议头非法");
          break;
        }
        if (buf.size() < need) break;

        if (type == bgm1::kTypeReply) {
          bgm1::Reply r;
          if (bgm1::parse_reply(buf.data(), need, &r) != bgm1::Err::kOk) {
            fail("REPLY 解析失败");
            break;
          }
          tracker_.on_reply(r);
          // 04 号 C：REPLY 经 msgq 转交 modeld（outputs[0:2066) 与遥测原样镜像，
          // 逐帧都发——App 侧解码跳帧的全零 outputs 也发，落回小模型由 modeld 判）
          MessageBuilder msg;
          auto evt = msg.initEvent();
          auto br = evt.initBigModelReply();
          br.setFrameIdx(r.frame_idx);
          br.setTEof(r.t_eof);
          br.setFlags(r.flags);
          auto outs = br.initOutputs(bgm1::kReplyOutputsCount);
          for (size_t i = 0; i < bgm1::kReplyOutputsCount; i++) outs.set(i, r.outputs[i]);
          auto tel = br.initTelemetry(bgm1::kReplyTelemetryCount);
          for (size_t i = 0; i < bgm1::kReplyTelemetryCount; i++) tel.set(i, r.telemetry[i]);
          pm_.send("bigModelReply", msg);
        } else {
          bgm1::ErrMsg e;
          if (bgm1::parse_err(buf.data(), need, &e) == bgm1::Err::kOk) {
            tracker_.on_err(e);
            LOGE("bigmodeld: 服务端 ERR code=%u detail=%u frame_idx=%u", e.code, e.detail, e.frame_idx);
          }
          sender_->notify_disconnect();
          buf.clear();
          break;
        }
        buf.erase(buf.begin(), buf.begin() + need);
      }
    }
  }

  // ===== 标定/输入元数据线程：extrinsicsCalibration.rpyCalib + modelDataV2SP → 帧头 =====
  void calib_thread() {
    SubMaster sm({"extrinsicsCalibration", "modelDataV2SP"});
    while (!do_exit) {
      sm.update(1000);
      if (sm.updated("extrinsicsCalibration")) {
        auto c = sm["extrinsicsCalibration"].getExtrinsicsCalibration();
        auto rpy = c.getRpyCalib();
        if (rpy.size() == 3) {
          float r[3] = {rpy[0], rpy[1], rpy[2]};
          meta_.set_rpy(r);
        }
      }
      // 04 号 C-2：modeld 权威元数据（电平采样，迟到 ≤2 帧按 04 号口径接受）
      if (sm.updated("modelDataV2SP")) {
        auto sp = sm["modelDataV2SP"].getModelDataV2SP();
        auto at = sp.getBigActionT();
        if (at.size() == 2) {
          float a[2] = {at[0], at[1]};
          meta_.set_model_inputs(a, sp.getDesireClass());
        }
      }
    }
  }

 private:
  // 帧负载：配对状态机槽内携带（配对键/序号在 PairMatcher::Frame 上）
  struct Slot {
    VisionIpcBufExtra extra = {};
    VisionBuf* buf = nullptr;
  };

  void init_encoder(StreamId sid, const VisionBuf& bi) {
    LOGW("bigmodeld: %s 编码器 init %zux%zu", sid == kRoad ? "road" : "wide", bi.width, bi.height);

    // 编码缓冲池：plane.length ≥ sizeimage（21 号 EINVAL 坑）
    ctx_[sid].pool.init(std::max(bi.len, kNv12SizeImage), bi.width, bi.height, bi.stride, bi.uv_offset);

    EncoderInfo info{};
    info.publish_name = sid == kRoad ? "bgmRoad" : "bgmWide";  // 仅日志标识（不建 PubMaster）
    info.fps = kFps;
    int bitrate = bitrate_;
    info.get_settings = [bitrate](int) {
      return EncoderSettings{.encode_type = cereal::EncodeIndex::Type::FULL_H_E_V_C,
                             .bitrate = bitrate,
                             .gop_size = kGopSize,
                             .b_frames = 0};
    };

    V4LEncoder::Options opt;
    opt.output_callback = [this, sid](int, uint32_t, VisionIpcBufExtra& extra, unsigned int flags,
                                      kj::ArrayPtr<capnp::byte> header, kj::ArrayPtr<capnp::byte> dat) {
      on_encoded(sid, extra, flags, header, dat);
    };
    opt.input_done_callback = [this, sid](VisionBuf* b) { ctx_[sid].pool.release(b); };

    ctx_[sid].enc = std::make_unique<V4LEncoder>(info, (int)bi.width, (int)bi.height, opt);
    ctx_[sid].enc->encoder_open();
  }

  // ---- 配对 / 编码（持 state_mtx）----
  // 配对键 = timestamp_sof 邻近（见 pair_matcher.h：真机实测两路 frame_id 是各自
  // 独立的出帧计数器、持续漂移，不能作配对键）。配对死亡只计数/日志，不上报调度器；
  // road 死亡帧的 SOF 仍参与时间槽编号，wide 死亡帧不参与编号。
  void place_frame(StreamId sid, const VisionIpcBufExtra& extra, VisionBuf* buf) {
    std::lock_guard<std::mutex> lk(state_mtx_);
    drain_events_locked();

    const bool is_road = (sid == kRoad);
    bgm::PairMatcher<Slot>::Frame f{extra.frame_id, extra.timestamp_sof, Slot{extra, buf}};
    bgm::PairMatcher<Slot>::Actions a = matcher_.push(is_road, f);

    if (a.kill_road) {
      account_pair_drop_locked(true, a.road_dead);
      ctx_[kRoad].pool.release(a.road_dead.payload.buf);
    }
    if (a.kill_wide) {
      account_pair_drop_locked(false, a.wide_dead);
      ctx_[kWide].pool.release(a.wide_dead.payload.buf);
    }
    if (a.pair) {
      encode_pair_locked(a.road.payload, a.wide.payload);
    }
  }

  void encode_pair_locked(Slot road, Slot wide) {
    const CamFrameId frame_id{road.extra.frame_id};
    const FrameIdx frame_idx = indexer_.index(road.extra.timestamp_sof);

    // I 帧事件必须先于本帧 encode_frame（21 号实测「下一帧立即 I 帧」）：
    // 本帧自己的预测 + 事件累积的请求都在此刻发出（落点 = 事件后第一个提交帧），
    // 且此时两路编码器必已就绪（成对即两路都已 init_encoder）
    SchedStep s = sched_.on_frame_submit(frame_idx);
    accumulate_requests_locked(s);
    if (need_req_road_) {
      ctx_[kRoad].enc->request_keyframe();
      need_req_road_ = false;
    }
    if (need_req_wide_) {
      ctx_[kWide].enc->request_keyframe();
      need_req_wide_ = false;
    }

    update_bitrate_locked();

    bgm1::FrameHeader hdr;
    meta_.fill(road.extra.timestamp_eof, &hdr);
    hdr.frame_idx = u32(frame_idx);

    OutMeta om;
    om.frame_id = frame_id;
    om.frame_idx = frame_idx;
    om.conn_epoch = ConnEpoch{cur_epoch_.load(std::memory_order_relaxed)};
    om.road_idr_pred = s.road_idr;
    om.wide_idr_pred = s.wide_idr;
    om.hdr = hdr;
    ctx_[kRoad].meta.push(om);
    om.hdr = {};
    // 查表键 = 各路自己的 frame_id（on_encoded 按本路 extra.frame_id 取回；两路
    // frame_id 计数器漂移，不能混用同一个键）
    om.frame_id = CamFrameId{wide.extra.frame_id};
    ctx_[kWide].meta.push(om);

    // 两路同时提交编码；缓冲由 input_done_callback 归还池
    ctx_[kRoad].enc->encode_frame(road.buf, &road.extra);
    ctx_[kWide].enc->encode_frame(wide.buf, &wide.extra);
  }

  void account_pair_drop_locked(bool is_road, const bgm::PairMatcher<Slot>::Frame& dead) {
    if (is_road) {
      const FrameIdx frame_idx = indexer_.index(dead.timestamp_sof);
      const uint64_t count = ++pair_drop_road_count_;
      LOGW("bigmodeld: 配对缺帧（road，仅记账）frame_id=%u frame_idx=%u count=%llu",
           dead.frame_id, u32(frame_idx), (unsigned long long)count);
    } else {
      const uint64_t count = ++pair_drop_wide_count_;
      LOGW("bigmodeld: 配对缺帧（wide，仅记账）frame_id=%u count=%llu",
           dead.frame_id, (unsigned long long)count);
    }
  }

  // 请求不在此刻执行、只累积（flush 在 encode_pair_locked，保证编码器已就绪且落点正确）
  void accumulate_requests_locked(const SchedStep& s) {
    need_req_road_ |= s.request_keyframe_road;
    need_req_wide_ |= s.request_keyframe_wide;
  }

  // Params "BigmodelEncoderBitrate" 每帧读（仿 encoderd.cc:54-60）；--bitrate 为初值
  void update_bitrate_locked() {
    static Params params;
    int b = bitrate_;
    std::string val = params.get("BigmodelEncoderBitrate");
    if (!val.empty()) {
      int v = std::atoi(val.c_str());
      if (v > 0) b = v;
    }
    if (b == cur_bitrate_) return;
    ctx_[kRoad].enc->set_bitrate(b);
    ctx_[kWide].enc->set_bitrate(b);
    cur_bitrate_ = b;
    LOGW("bigmodeld: 码率切换 %d bps", b);
  }

  // ---- sender 事件（sender.mtx 锁内回调：只记账 + 推队列 + 日志，不取 state_mtx）----
  void on_uplink_event(const UplinkEventInfo& e) {
    switch (e.ev) {
      case UplinkEvent::kNewConnection:
        LOGW("bigmodeld: 新连接（frame_idx 与 I 帧状态重置）");
        break;
      case UplinkEvent::kStall:
        LOGE("bigmodeld: 假死（≥200 ms 无进展）截断重连 frame_idx=%u", u32(e.frame_idx));
        break;
      case UplinkEvent::kLinkLost:
        LOGE("bigmodeld: 连接丢失（连续重连失败，持续重试中）");
        break;
      case UplinkEvent::kDrop:
        LOGW("bigmodeld: 丢帧（发送队列覆盖/编码输出缺帧）frame_idx=%u", u32(e.frame_idx));
        break;
      case UplinkEvent::kStreamGap:
        LOGW("bigmodeld: 码流断档（编码输出被吞，main 合成）");
        break;
      case UplinkEvent::kHeadBarrier:
        LOGW("bigmodeld: 序列头门丢弃（非双路 IDR 对不开流）frame_idx=%u", u32(e.frame_idx));
        break;
      case UplinkEvent::kFrameSent:
        break;  // 发送段报表在 writer 线程锁外做（见 writer_thread）
    }
    std::lock_guard<std::mutex> lk(ev_mtx_);
    evs_.push_back(e);
  }

  // 事件按序落状态机：request_keyframe 累积到下一次组帧前统一发出 = 事件后第一个提交帧
  void drain_events_locked() {
    std::vector<UplinkEventInfo> batch;
    {
      std::lock_guard<std::mutex> lk(ev_mtx_);
      batch.swap(evs_);
    }
    for (const auto& e : batch) {
      SchedStep s;
      switch (e.ev) {
        case UplinkEvent::kNewConnection:
          cur_epoch_.store(u32(e.conn_epoch), std::memory_order_relaxed);
          indexer_.reset();
          // 旧连接的编码在途输出不带进新连接（重连清空队列同款）
          ctx_[kRoad].meta.clear();
          ctx_[kWide].meta.clear();
          s = sched_.on_connect();
          break;
        case UplinkEvent::kDrop:
        case UplinkEvent::kHeadBarrier:
          // 码流断档 / 序列头门丢弃：都开新序列 + 双路 request（对的下一提交帧起 IDR）
          s = sched_.on_frame_dropped(e.frame_idx);
          break;
        case UplinkEvent::kStreamGap:
          // 编码输出被吞（情形 C）：无 frame_idx 键、无重复上报，不去重
          s = sched_.on_stream_gap();
          break;
        case UplinkEvent::kFrameSent:
          s = sched_.on_frame_sent(e.frame_idx);
          break;
        default:
          break;
      }
      accumulate_requests_locked(s);
    }
  }

  // ---- 编码输出回调（编码器 dequeue 线程）----
  void on_encoded(StreamId sid, VisionIpcBufExtra& extra, unsigned int flags,
                  kj::ArrayPtr<capnp::byte> header, kj::ArrayPtr<capnp::byte> dat) {
    MetaCache::Result r = ctx_[sid].meta.take(CamFrameId{extra.frame_id});
    if (r.st != MetaTake::kHit) {
      if (r.st == MetaTake::kStaleMiss) {
        // 旧连接/已清/已弹的迟到输出（连接建立窗口）：序列头门兜底，静默丢弃
        LOGW("bigmodeld: %s 旧连接迟到编码输出，丢弃 frame_id=%u",
             sid == kRoad ? "road" : "wide", extra.frame_id);
        return;
      }
      // 查无且非旧 = 编码输出被吞：码流断档（情形 C）——显式上报调度器
      //（新序列 + 双路 request）+ 序列头门重新关门（断档后首帧必须双路 IDR）。
      // kStreamGap 不入 frame_idx 键空间、不去重（同 frame_id 不会二次输出，
      // 跨路同值也不再互相吞掉——18 号 review 魔法位 hack 的收编，19 号 #2）。
      LOGE("bigmodeld: %s 编码输出无对应提交（码流断档）frame_id=%u",
           sid == kRoad ? "road" : "wide", extra.frame_id);
      sender_->notify_stream_gap();
      {
        std::lock_guard<std::mutex> lk(ev_mtx_);
        evs_.push_back({UplinkEvent::kStreamGap, FrameIdx{}});
      }
      return;
    }

    const OutMeta& om = r.om;
    // 命中前缀死条目（FIFO 下输出已丢）同样 = 码流断档：上报 + 关门（各路 dedup 到 frame_idx）
    if (!r.dead.empty()) {
      LOGE("bigmodeld: %s 编码输出缺帧 %zu 个（frame_idx=%u 起）码流断档",
           sid == kRoad ? "road" : "wide", r.dead.size(), u32(r.dead.front().frame_idx));
      sender_->notify_stream_gap();
      std::lock_guard<std::mutex> lk(ev_mtx_);
      evs_.push_back({UplinkEvent::kDrop, r.dead.front().frame_idx});
    }

    const bool keyframe = (flags & V4L2_BUF_FLAG_KEYFRAME) != 0;
    const uint8_t* data = reinterpret_cast<const uint8_t*>(dat.begin());
    size_t len = dat.size();
    if (keyframe && header.size() > 0) {
      // IDR AU 自带 VPS/SPS/PPS：v4l_encoder 每帧都给保存的 codec config，
      // 只在实际 keyframe 时拼在 AU 前（v4l_encoder.cc:120-133）
      EncoderCtx& c = ctx_[sid];
      c.au.resize(header.size() + dat.size());
      std::memcpy(c.au.data(), header.begin(), header.size());
      std::memcpy(c.au.data() + header.size(), dat.begin(), dat.size());
      data = c.au.data();
      len = c.au.size();
    }

    // 段长超协议上限（spec「线协议」默认 1 MiB，服务端判 BAD_FRAME）：帧死亡，
    // 不进发送路径（丢帧上报 + 新序列 + 序列头门关门，经事件队列按序落状态机）
    if (len > bgm1::kDefaultMaxSegment) {
      LOGE("bigmodeld: %s 段长 %zu 超上限 %u，丢帧 frame_idx=%u",
           sid == kRoad ? "road" : "wide", len, bgm1::kDefaultMaxSegment, u32(om.frame_idx));
      sender_->notify_stream_gap();
      std::lock_guard<std::mutex> lk(ev_mtx_);
      evs_.push_back({UplinkEvent::kDrop, om.frame_idx});
      return;
    }

    if (sid == kRoad) {
      // bit0 = road 实际 keyframe 位（精确）、bit1 = wide IDR 策略预测
      sender_->submit_road(om.conn_epoch, om.frame_idx, om.hdr, data, len,
                           keyframe, om.road_idr_pred, om.wide_idr_pred);
    } else {
      sender_->submit_wide(om.conn_epoch, om.frame_idx, data, len, keyframe);
    }
  }

  // ---- 成员（锁序：sender_ > state_mtx_ > {ev_mtx_, MetaCache, BufPool}）----
  MetaProvider meta_;
  TcpSocket sock_;
  std::unique_ptr<UplinkSender> sender_;
  ReplyTracker tracker_;
  PubMaster pm_{{"bigModelReply"}};  // 04 号 C：REPLY 经 msgq 转交 modeld

  std::mutex state_mtx_;
  FrameScheduler sched_;
  FrameIndexer indexer_;
  bgm::PairMatcher<Slot> matcher_;
  uint64_t pair_drop_road_count_ = 0;
  uint64_t pair_drop_wide_count_ = 0;
  bool need_req_road_ = false;  // 事件累积的 request_keyframe，组帧前统一 flush
  bool need_req_wide_ = false;
  // 连接代号（sender 建连次数，kNewConnection 事件带出）：组帧时 stamp 进 OutMeta，
  // submit 侧与 sender 当前代号不符即拒（重连窗口内旧输出不得混进新连接）
  std::atomic<uint32_t> cur_epoch_{0};  // ConnEpoch 的裸存储（atomic 强类型无加法）

  std::mutex ev_mtx_;
  std::vector<UplinkEventInfo> evs_;

  EncoderCtx ctx_[2];
  int bitrate_;
  int cur_bitrate_;
};

void usage() {
  fprintf(stderr, "usage: bigmodeld [--host HOST] [--port PORT] [--bitrate BPS]\n");
}

}  // namespace

int main(int argc, char* argv[]) {
  std::string host = "127.0.0.1";  // adb reverse 验证默认连本机
  int port = 7070;
  int bitrate = kDefaultBitrate;

  for (int i = 1; i < argc; i++) {
    std::string arg = argv[i];
    auto need_val = [&](const char* what) -> const char* {
      if (i + 1 >= argc) {
        fprintf(stderr, "bigmodeld: %s 缺参数值\n", what);
        usage();
        exit(2);
      }
      return argv[++i];
    };
    if (arg == "--host") {
      host = need_val("--host");
    } else if (arg == "--port") {
      port = std::atoi(need_val("--port"));
    } else if (arg == "--bitrate") {
      bitrate = std::atoi(need_val("--bitrate"));
    } else {
      fprintf(stderr, "bigmodeld: 未知参数 %s\n", arg.c_str());
      usage();
      return 2;
    }
  }
  if (port <= 0 || port > 65535 || bitrate <= 0) {
    usage();
    return 2;
  }

  LOGW("bigmodeld: start host=%s port=%d bitrate=%d", host.c_str(), port, bitrate);

  Bigmodeld bg(host, port, bitrate);
  std::vector<std::thread> ts;
  ts.emplace_back(&Bigmodeld::capture_thread, &bg, kRoad);
  ts.emplace_back(&Bigmodeld::capture_thread, &bg, kWide);
  ts.emplace_back(&Bigmodeld::writer_thread, &bg);
  ts.emplace_back(&Bigmodeld::reply_thread, &bg);
  ts.emplace_back(&Bigmodeld::calib_thread, &bg);
  for (auto& t : ts) t.join();

  LOGW("bigmodeld: exit");
  return 0;
}
