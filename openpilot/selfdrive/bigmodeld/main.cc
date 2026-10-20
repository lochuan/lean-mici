// bigmodeld：C4 上行进程（16 号）。生命周期跟随 modeld（process_config only_onroad）：
//   camered VisionIPC 两路取帧 → warp 成 512×256 NV12 进编码缓冲（协议 v2，App 不再 warp）
//   → 按 frame_id 配对 → V4L 双路硬编
//   （HEVC Main、VBR、无 B 帧、GOP 20、默认 2 Mb/s/路可调）→ 10 号布局组 FRAME
//   经 TCP 发出（road 出包即发）；REPLY 独立线程收下解析并转交 modeld。不落盘、不进 loggerd
//   （设了编码输出回调 ⇒ 不建 PubMaster ⇒ loggerd 无编码数据）。
//
// 实现口径见本目录 README「实现口径」；纯逻辑在 frame_scheduler / uplink_sender /
// frame_meta / frame_codec，宿主单测 test_bigmodeld.cc。
//
// 线程与锁序（全文件统一，防死锁）：
//   取帧×2（state_mtx：配对/状态机/索引 + 编码缓冲池）、编码器 dequeue×2（输出回调：
//   meta 缓存 → sender.submit_*）、writer（sender.step）、REPLY reader（read_some →
//   msg_framer → parse → msgq + navStateSP + /dev/shm/params）、
//   标定/输入元数据（SubMaster → MetaProvider.set_rpy/set_model_inputs）。
//   标定线程另订阅 gpsLocationExternal(10 Hz) / deviceMotion(20 Hz)；msgq 每条消息对每个订阅线程
//   发 SIGUSR2（与是否 poll 无关），因此线程每秒会多约 30 次信号唤醒，但 SubMaster 只 poll 原有
//   节拍源，用户态循环不会多跑。
//   锁序：sender.mtx > state_mtx > {ev_mtx, meta 缓存, buf 池, 分段计时}（叶子锁，绝不反向嵌套）。
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
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "common/params.h"
#include "common/timing.h"
#include "common/util.h"
#include "cereal/messaging/messaging.h"
#include "system/loggerd/encoder/v4l_encoder.h"
#include "system/loggerd/loggerd.h"

#include <media/msm_media_info.h>

#include "frame_codec.h"
#include "frame_meta.h"
#include "frame_scheduler.h"
#include "frame_stages.h"
#include "meta_cache.h"
#include "msg_framer.h"
#include "nav_outputs.h"
#include "pair_matcher.h"
#include "server_locator.h"
#include "uplink_sender.h"
#include "warp_pack.h"

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

ExitHandler do_exit;

namespace {

constexpr int kFps = 20;
constexpr int kGopSize = 20;
constexpr int kDefaultBitrate = 2'000'000;  // 2 Mb/s/路（512×256 下约 4 KB/帧/路）
// 编码输入 = warp 后 512×256 NV12，按 venus 对齐布局（QBUF 要求 plane.length ≥ S_FMT sizeimage，21 号 EINVAL 坑）
const size_t kEncStride = VENUS_Y_STRIDE(COLOR_FMT_NV12, chipmunk::kModelW);
const size_t kEncUvOffset = kEncStride * VENUS_Y_SCANLINES(COLOR_FMT_NV12, chipmunk::kModelH);
const size_t kEncBufSize = VENUS_BUFFER_SIZE(COLOR_FMT_NV12, chipmunk::kModelW, chipmunk::kModelH);
constexpr int kEncPoolDepth = 6;   // 每路编码缓冲池（1 配对槽 + ≤5 在编码器在途）
constexpr int kSchedFifoPrio = 53;
// 下列 RTT/取帧/REPLY 数值测于 os04c10 读出常量修正前（timestamp_eof 早 ~21.5 ms）：以 eof 为零点的量相对真实 eof 各减 ~21.5 ms。
// writer/reply 线程：core 3 有 pandad（FIFO 54，常驻 ~36%）抢占 → 编码完到 send 等 1～8 ms；
// core 2 只有 RT 5 的 locationd 系（台架 A/B/A：RTT p90 62.4→56.2 ms）。EINVAL（离线核）容忍
const std::vector<int> kCpuAffinity = {2};
// capture 线程做 warp（查找表 ~0.3 ms/路，标定变化那帧建表 ~1 ms），FIFO 50。
// core 4/5 上有 FIFO 53 的 controlsd/card/ui，capture 排队使取帧 p50 比 camerad 出帧晚 ~7 ms。
// core 7 有 modeld（FIFO 54，每帧跑 ~9.6 ms）：road 在此每帧排队 ~4.3 ms，取帧 p50 30.1 ms（modeld 24.4 ms）。
// core 6 有 camerad（TS）：wide 的短突发抢占它，让 road 帧对所有订阅者晚 ~0.7 ms；road 也放这里则 modeld 晚 ~1 ms。
// road→core 2（RT 5 的 locationd 系 + 本进程 writer/reply，帧到达时都被抢占或错开）、wide→core 6，
// 台架 jungle 实测：取帧 p50 30.1→24.5 ms，大模型 REPLY p50 54.6→49.6 ms / p99 61.2→53.9 ms，
// modeld 收帧不变，locationd inputsOK 无异常。wide 也放 core 2 只再快 ~0.7 ms，却让 FIFO 50 线程
// 更多压在 locationd 所在核（eagled 曾因高优先级拖慢 sensord 致 locationdTemporaryError），不取。
constexpr int kCaptureFifoPrio = 50;
const std::vector<int> kCaptureCore[2] = {{2}, {6}};
// writer/reply 空闲时只等事件，此值只是 do_exit 检查周期（同取帧 vipc.recv 的 100 ms）
constexpr int kIdleWaitMs = 100;

enum StreamId { kRoad = 0, kWide = 1 };

// 每路线程标识：取帧线程日志名 / 编码器名（V4LEncoder 据此把 dequeue 线程命名为 "dq-" + 编码器名，
// v4l_encoder.cc:74）
struct StreamThreadNames {
  const char* capture;
  const char* encoder;
};
constexpr StreamThreadNames kThreadNames[2] = {{"bgm_road", "bgmRoad"}, {"bgm_wide", "bgmWide"}};

// NAV_STATE.nav_status（线上 0–7）→ capnp enum；线上越界按 fault（协议只定义 0–7）
cereal::NavStateSP::NavState::Status nav_status_from_wire(uint8_t status) {
  using Status = cereal::NavStateSP::NavState::Status;
  switch (status) {
    case 0: return Status::NOT_RUNNING;
    case 1: return Status::IDLE;
    case 2: return Status::NAVIGATING;
    case 3: return Status::REROUTING;
    case 4: return Status::POSITION_LOST;
    case 5: return Status::ARRIVED;
    case 6: return Status::MAP_MISSING;
    default: return Status::FAULT;
  }
}

void setup_realtime(const char* who, int prio = kSchedFifoPrio, const std::vector<int>& cores = kCpuAffinity) {
  if (util::set_realtime_priority(prio) != 0) {
    LOGW("bigmodeld: %s SCHED_FIFO %d 失败 errno=%d", who, prio, errno);
  }
  if (util::set_core_affinity(cores) != 0) {
    LOGW("bigmodeld: %s 绑核失败（容忍）errno=%d", who, errno);
  }
}

// ---- TCP 上行 socket（可注入接缝的系统实现）----
// 非阻塞：write_some/read_some 语义对齐 UplinkSocket（>0 进展 / 0 会阻塞 / -1 错或 EOF）。
// 连接目标由 ServerLocator 给（06 号：手动 IP / 限当前 Wi-Fi 子网的 mDNS 发现）；
// 每次建连失败报 on_connect_failure（发现节流）。
class TcpSocket : public UplinkSocket {
 public:
  explicit TcpSocket(ServerLocator* loc) : loc_(loc) {}
  ~TcpSocket() override { close(); }

  bool connect(int timeout_ms) override {
    std::lock_guard<std::mutex> lk(mtx_);
    close_locked();

    ServerEndpoint ep;
    if (!loc_->resolve(&ep)) {
      loc_->on_connect_failure();  // 没找到服务也算一次失败（持续重试）
      return false;
    }

    struct addrinfo hints = {}, *res = nullptr;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char portstr[16];
    std::snprintf(portstr, sizeof portstr, "%d", int(ep.port));
    if (getaddrinfo(ep.host.c_str(), portstr, &hints, &res) != 0 || res == nullptr) {
      loc_->on_connect_failure();
      return false;
    }

    int fd = ::socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd < 0) {
      freeaddrinfo(res);
      loc_->on_connect_failure();
      return false;
    }
    int fl = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);

    int rc = ::connect(fd, res->ai_addr, res->ai_addrlen);
    freeaddrinfo(res);
    if (rc != 0) {
      if (errno != EINPROGRESS) {
        ::close(fd);
        loc_->on_connect_failure();
        return false;
      }
      struct pollfd pfd = {.fd = fd, .events = POLLOUT, .revents = 0};
      if (poll(&pfd, 1, timeout_ms) <= 0) {
        ::close(fd);
        loc_->on_connect_failure();
        return false;
      }
      int err = 0;
      socklen_t el = sizeof err;
      if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &el) != 0 || err != 0) {
        ::close(fd);
        loc_->on_connect_failure();
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

  // 可读即返回（REPLY 到达即醒，不睡满轮询周期）；poll 在锁外，免得挡住发送线程。
  // 发送线程断连走 close_locked 的 shutdown，会让在等的 poll 立即返回（POLLHUP），调用方随后
  // 重查连接状态；只有「取 fd 后、进 poll 前」被关且编号被复用这一极窄窗口会睡满 timeout_ms。
  void wait_readable(int timeout_ms) {
    int fd;
    {
      std::lock_guard<std::mutex> lk(mtx_);
      fd = fd_;
    }
    if (fd < 0) return;
    struct pollfd pfd = {.fd = fd, .events = POLLIN, .revents = 0};
    poll(&pfd, 1, timeout_ms);
  }

 private:
  void close_locked() {
    if (fd_ >= 0) {
      // 单 close 唤不醒别的线程里在等这个 fd 的 poll（poll 持有文件引用）；shutdown 才会
      ::shutdown(fd_, SHUT_RDWR);
      ::close(fd_);
      fd_ = -1;
    }
  }

  std::mutex mtx_;
  int fd_ = -1;
  ServerLocator* loc_;
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
  Bigmodeld(ServerLocator* loc, int bitrate)
      : sock_(loc), bitrate_(bitrate), cur_bitrate_(bitrate) {
    sender_ = std::make_unique<UplinkSender>(
        &sock_, [] { return (uint64_t)millis_since_boot(); }, UplinkSenderConfig{},
        [this](const UplinkEventInfo& e) { on_uplink_event(e); });
    // 07 号：进程启动即写 connecting——BigmodelLinkState 保留上趟旧值，
    // 不写则新一趟起步会读到上趟 connected 闪绿
    link_mark_connecting();
  }

  // ===== 取帧/配对线程（×2）=====
  void capture_thread(StreamId sid) {
    setup_realtime(kThreadNames[sid].capture, kCaptureFifoPrio, kCaptureCore[sid]);

    VisionStreamType type = sid == kRoad ? VISION_STREAM_NARROW_ROAD : VISION_STREAM_WIDE_ROAD;
    VisionIpcClient vipc("camerad", type, false);
    chipmunk::WarpLut warp_lut;  // 本线程独占：标定不变时每帧只按表取像素
    bool inited = false;

    while (!do_exit) {
      if (!vipc.connect(false)) {
        util::sleep_for(100);
        continue;
      }
      if (!inited) {
        init_encoder(sid);
        inited = true;
      }
      while (!do_exit) {
        VisionIpcBufExtra extra;
        VisionBuf* buf = vipc.recv(&extra, 100);
        if (buf == nullptr) continue;
        // 上游覆盖/滞后（缓冲被新帧顶掉）：不进编码器；后续 SOF 可形成时间槽空洞
        if (buf->get_frame_id() != extra.frame_id) continue;
        const uint64_t recv_ns = nanos_since_boot();
        // warp 进编码缓冲后立即可放回 VisionIPC 缓冲（配对等待不占上游缓冲）
        VisionBuf* dst = ctx_[sid].pool.acquire();
        float mat[9];
        meta_.warp(sid == kWide, mat);
        const chipmunk::Nv12View src{buf->y, buf->uv, (int)buf->width, (int)buf->height,
                                     (int)buf->stride, (int)buf->stride};
        if (!warp_lut.warp(src, mat, chipmunk::Nv12Out{dst->y, dst->uv, (int)dst->stride, (int)dst->stride})) {
          LOGE("bigmodeld: %s warp 失败（源 %zux%zu stride %zu）", sid == kRoad ? "road" : "wide",
               buf->width, buf->height, buf->stride);
          ctx_[sid].pool.release(dst);
          continue;
        }
        place_frame(sid, extra, dst, mat, recv_ns, nanos_since_boot());
      }
    }
  }

  // ===== 发送线程 =====
  void writer_thread() {
    setup_realtime("bgm_writer");
    while (!do_exit) {
      if (!sender_->step()) sender_->wait_for_work(kIdleWaitMs);
    }
  }

  // ===== REPLY 接收线程（(e)）=====
  void reply_thread() {
    setup_realtime("bgm_reply");
    MsgFramer framer;

    // 坏流/EOF/ERR 统一收尾：记断连 + 走重连路径 + 清半包缓冲（16 号 code-review：Duplicated Code）。
    // peer_ver 只在头部可信时传入（>=0）；EOF / 首条非 HELLO 没有可信版本。
    auto fail = [&](const char* why, int peer_ver = -1) {
      if (peer_ver >= 0) {
        LOGE("bigmodeld: REPLY 流异常（%s），断连重连 got_ver=%d want_ver=%u", why, peer_ver,
             unsigned(bgm1::kVersion));
      } else {
        LOGE("bigmodeld: REPLY 流异常（%s），断连重连", why);
      }
      sender_->notify_disconnect();
      link_mark_connecting();
      framer.clear();
      // S2：断连即丢弃 NavExt 摘要；旧弯道点不得继续驱动纵向（MapTargetVelocities → []）
      link_session_.on_disconnect(nav_link_tick());
      publish_map_targets();
    };

    while (!do_exit) {
      // R1：连接代号变化 = drop 后 writer 线程已重连（reply 线程可能还没看到 !connected）。
      // session 复位 hello 标志 / NavExt 摘要并清弯道目标；半包缓冲在这里清。
      if (link_session_.on_epoch(u32(sender_->conn_epoch()), [&] { return nav_link_tick(); })) {
        framer.clear();
        publish_map_targets();
      }
      if (!sender_->connected()) {
        // 未连接/启动即清空：不留下上一趟或本趟崩溃前的弯道点
        link_session_.on_idle(false, nav_link_tick());
        publish_map_targets();
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
        sock_.wait_readable(kIdleWaitMs);
        // 无新 REPLY：NavExt / GNSS 过期时清掉旧弯道点（writer 只在需要时写）
        link_session_.on_idle(sender_->connected(), nav_link_tick());
        publish_map_targets();
        continue;
      }
      framer.append(tmp, n);

      // MsgHdr 分流：HELLO/REPLY/ERR 定长整条解析，坏流即断连重来
      while (true) {
        MsgFramer::Message framed;
        const MsgFramer::Result fr = framer.next(&framed);
        if (fr == MsgFramer::Result::kNeedMore) break;
        const uint8_t got_ver = framer.peer_version();
        const uint8_t* data = framer.data();
        const size_t need = framed.size;
        if (fr == MsgFramer::Result::kBadHeader) {
          const uint32_t len = bgm1::read_u32_le(data + 12);
          LOGE("bigmodeld: REPLY 流协议错 type=0x%02x len=%u got_ver=%u want_ver=%u", framed.type, len,
               unsigned(got_ver), unsigned(bgm1::kVersion));
          fail("协议头非法", got_ver);
          break;
        }

        if (framed.type == bgm1::kTypeHello) {
          // 06 号：HELLO 必须是连接首条消息且每连接仅一条（ver/instance_id/max_frame）
          bgm1::Hello h;
          bgm1::Err pe = link_session_.hello_seen() ? bgm1::Err::kBadType : bgm1::parse_hello(data, need, &h);
          if (pe != bgm1::Err::kOk) {
            if (pe == bgm1::Err::kBadVersion) {
              // N12：先置 version_mismatch，再 fail()（connecting 被 sticky 状态挡住，
              // UI 不闪；fail() 自带唯一一条带 got/want 的日志）
              link_mark_version_mismatch();
              fail("HELLO 版本不符（协议版本不符）", got_ver);
            } else {
              LOGE("bigmodeld: HELLO 解析失败 err=%d got_ver=%u want_ver=%u", int(pe),
                   unsigned(got_ver), unsigned(bgm1::kVersion));
              fail("HELLO 非法/重复", got_ver);
            }
            break;
          }
          link_session_.on_hello_ok();
          max_segment_.store(h.max_frame, std::memory_order_relaxed);  // 段长上限以 HELLO 为准（spec）
          link_.on_hello(h.instance_id);
          link_state_write();
          LOGW("bigmodeld: HELLO instance_id=0x%llx max_frame=%u（%s）",
               (unsigned long long)h.instance_id, h.max_frame, link_.value().c_str());
        } else if (!link_session_.hello_seen()) {
          fail("首条消息不是 HELLO");
          break;
        } else if (framed.type == bgm1::kTypeReply) {
          const uint64_t reply_ns = nanos_since_boot();
          bgm1::Reply r;
          const bgm1::Err re = bgm1::parse_reply(data, need, &r);
          if (re != bgm1::Err::kOk) {
            LOGE("bigmodeld: REPLY 解析失败 err=%d got_ver=%u want_ver=%u", int(re), unsigned(got_ver),
                 unsigned(bgm1::kVersion));
            fail("REPLY 解析失败", got_ver);
            break;
          }
          // 04 号 C：REPLY 经 msgq 转交 modeld（outputs[0:2066) 与 flags 原样镜像，
          // 逐帧都发——App 侧解码跳帧的全零 outputs 也发，落回小模型由 modeld 判）
          MessageBuilder msg;
          auto evt = msg.initEvent();
          auto br = evt.initBigModelReply();
          br.setTEof(r.t_eof);
          br.setFlags(r.flags);
          auto outs = br.initOutputs(bgm1::kReplyOutputsCount);
          for (size_t i = 0; i < bgm1::kReplyOutputsCount; i++) outs.set(i, r.outputs[i]);
          auto st = br.initStages();
          st.setPhoneTotalMs(r.telemetry[3] / 1e3f);
          StageMs s;
          if (stages_.on_reply(FrameIdx{r.frame_idx}, reply_ns, &s)) {
            st.setCaptureMs(s.capture);
            st.setWarpMs(s.warp);
            st.setPairWaitMs(s.pair_wait);
            st.setEncodeMs(s.encode);
            st.setSendMs(s.send);
            st.setReplyWaitMs(s.reply_wait);
          }
          pm_.send("bigModelReply", msg);
          // P1：回包给 modeld 之后再做 navStateSP 与 map 目标（回包路径不做多余的事）
          publish_nav_state(r);
          // R3：决策时刻取 REPLY 处理完之后（nav_link_tick），而非 parse 前的 reply_ns
          link_session_.on_reply(reply_ns, r.t_eof, r.nav_ext, nav_link_tick());
          publish_map_targets();
        } else {
          bgm1::ErrMsg e;
          const bgm1::Err ee = bgm1::parse_err(data, need, &e);
          if (ee == bgm1::Err::kOk) {
            LOGE("bigmodeld: 服务端 ERR code=%u detail=%u frame_idx=%u", e.code, e.detail, e.frame_idx);
          } else {  // 版本真不一致时对端 ERR 的 ver 也不同，会落在这里
            LOGE("bigmodeld: 服务端 ERR 解析失败 err=%d got_ver=%u want_ver=%u", int(ee), unsigned(got_ver),
                 unsigned(bgm1::kVersion));
          }
          sender_->notify_disconnect();
          link_mark_connecting();
          framer.clear();
          link_session_.on_disconnect(nav_link_tick());
          publish_map_targets();
          break;
        }
        framer.consume(need);
      }
    }
    // AU-5：退出循环不留旧弯道点；calib 线程退出前还会再写一次
    link_session_.on_disconnect(nav_link_tick());
    publish_map_targets();
  }

  // ===== 标定/输入元数据线程：extrinsicsCalibration.rpyCalib + modelDataV2SP → 帧头 =====
  // gpsLocationExternal(10 Hz) / deviceMotion(20 Hz) 只在消息更新时取最新一条，不进 poll：
  // SubMaster 只 poll 原有 4 Hz + 20 Hz 节拍源，用户态循环不会因它们多跑。注意 msgq 的每条
  // 消息都会对每个订阅线程发 SIGUSR2（msgq_msg_send 末尾的 thread_signal），所以线程每秒仍会
  // 被这两路额外信号唤醒约 30 次；这是 msgq 语义，不是 poll 集合能避免的。
  // SubMaster 的 socket 是 conflate=true（socketmaster.cc:53）；update() 在 poll 返回后对未 poll
  // 的服务做非阻塞 receive(true)，拿到各自最新一条，队列不积压。
  void calib_thread() {
    SubMaster sm({"extrinsicsCalibration", "modelDataV2SP", "gpsLocationExternal", "deviceMotion"},
                 {"extrinsicsCalibration", "modelDataV2SP"});
    // 车体系换算用的 rpyCalib：与 MetaProvider.set_rpy 同一份（最近一条 extrinsics）。
    float rpy[3] = {0.f, 0.f, 0.f};
    // m-b：只有 calStatus==invalid 禁用 yaw/speed；uncalibrated/recalibrating 照常用当前 rpy。
    ExtrinsicsCalibStatus calib_status = ExtrinsicsCalibStatus::kUncalibrated;

    while (!do_exit) {
      sm.update(1000);
      // P2：mem Params 的文件 I/O 全在非实时 calib 线程；本线程本来就被 20 Hz 节拍源唤醒
      write_pending_map_targets();
      if (sm.updated("extrinsicsCalibration")) {
        auto c = sm["extrinsicsCalibration"].getExtrinsicsCalibration();
        auto calib_rpy = c.getRpyCalib();
        if (calib_rpy.size() == 3) {
          rpy[0] = calib_rpy[0];
          rpy[1] = calib_rpy[1];
          rpy[2] = calib_rpy[2];
          meta_.set_rpy(rpy);
        }
        switch (c.getCalStatus()) {
          case cereal::ExtrinsicsCalibration::Status::CALIBRATED:
            calib_status = ExtrinsicsCalibStatus::kCalibrated;
            break;
          case cereal::ExtrinsicsCalibration::Status::INVALID:
            calib_status = ExtrinsicsCalibStatus::kInvalid;
            break;
          case cereal::ExtrinsicsCalibration::Status::RECALIBRATING:
            calib_status = ExtrinsicsCalibStatus::kRecalibrating;
            break;
          default:
            calib_status = ExtrinsicsCalibStatus::kUncalibrated;
            break;
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
      // v3 GNSS（协议 §1.2.1）：u-blox 10 Hz；event_valid 也检查（B5，ubloxd 恒 true）。
      if (sm.updated("gpsLocationExternal")) {
        auto g = sm["gpsLocationExternal"].getGpsLocationExternal();
        GnssSample s;
        s.latitude_deg = g.getLatitude();
        s.longitude_deg = g.getLongitude();
        s.bearing_deg = g.getBearingDeg();
        s.horizontal_accuracy_m = g.getHorizontalAccuracy();
        s.bearing_accuracy_deg = g.getBearingAccuracyDeg();
        s.has_fix = g.getHasFix();
        s.event_valid = sm["gpsLocationExternal"].getValid();
        s.message_time_ns = sm["gpsLocationExternal"].getLogMonoTime();
        meta_.set_gnss(s);
        // S3：位置按 GNSS 节拍（u-blox 10 Hz）写；S7 精度 ≤30 m、无 fix / fix 过期不写
        if (const auto json = last_gps_position_json(s, nanos_since_boot())) {
          write_mem_param("LastGPSPosition", *json);
        }
      }
      // v3 VEHICLE（协议 §1.2.2）：deviceMotion 20 Hz，经 rpyCalib 转到车体系。
      // 消息 → 车体系样本的换算在纯函数里（可单测）；只在消息更新时调 set_vehicle（m6）。
      if (sm.updated("deviceMotion")) {
        auto dm = sm["deviceMotion"].getDeviceMotion();
        auto vel = dm.getVelocityDevice();
        auto ang = dm.getAngularVelocityDevice();
        const float v[3] = {vel.getX(), vel.getY(), vel.getZ()};
        const float w[3] = {ang.getX(), ang.getY(), ang.getZ()};
        DeviceMotionFlags f;
        f.event_valid = sm["deviceMotion"].getValid();
        f.sensors_ok = dm.getSensorsOK();
        f.inputs_ok = dm.getInputsOK();
        f.posenet_ok = dm.getPosenetOK();
        f.calib_status = calib_status;
        f.message_time_ns = sm["deviceMotion"].getLogMonoTime();
        meta_.set_vehicle(vehicle_sample_from_device_motion(rpy, v, w, f));
      }
    }
    // AU-5：退出前把 reply 线程最后交付的清空写出（所有线程 join 后 main 还会阻塞清一次）
    write_pending_map_targets();
  }

  // 退出清理（main 在所有线程 join 之后调用）：阻塞写，确保旧弯道点/位置不留给下次进程（S1a）
  void cleanup_map_params() {
    // 所有线程已 join，无并发读者/写者
    clear_map_targets(MapParamsOps{
        [this](const char* key, const std::string& value) { mem_params_.put(key, value); },
        [this](const char* key) { mem_params_.remove(key); }});
  }

 private:
  // 帧负载：配对状态机槽内携带（配对键/序号在 PairMatcher::Frame 上）
  struct Slot {
    VisionIpcBufExtra extra = {};
    VisionBuf* buf = nullptr;
    float mat[9] = {0};  // 本帧 warp 实际用的矩阵（帧头遥测记它，不在配对时重读标定）
    uint64_t recv_ns = 0, warped_ns = 0;  // 分段计时：VisionIPC 收到 / warp 完
  };

  void init_encoder(StreamId sid) {
    LOGW("bigmodeld: %s 编码器 init %dx%d", sid == kRoad ? "road" : "wide", chipmunk::kModelW, chipmunk::kModelH);

    ctx_[sid].pool.init(kEncBufSize, chipmunk::kModelW, chipmunk::kModelH, kEncStride, kEncUvOffset);

    EncoderInfo info{};
    info.publish_name = kThreadNames[sid].encoder;  // 仅日志标识（不建 PubMaster）
    info.fps = kFps;
    int bitrate = bitrate_;
    info.get_settings = [bitrate](int) {
      return EncoderSettings{.encode_type = cereal::EncodeIndex::Type::FULL_H_E_V_C,
                             .bitrate = bitrate,
                             .gop_size = kGopSize,
                             .b_frames = 0};
    };

    V4LEncoder::Options opt;
    opt.max_performance = true;  // venus realtime 优先级：512×256 编码 p90 5.1→1.9 ms（C4 实测）
    opt.output_callback = [this, sid](int, uint32_t, VisionIpcBufExtra& extra, unsigned int flags,
                                      kj::ArrayPtr<capnp::byte> header, kj::ArrayPtr<capnp::byte> dat) {
      on_encoded(sid, extra, flags, header, dat);
    };
    opt.input_done_callback = [this, sid](VisionBuf* b) { ctx_[sid].pool.release(b); };

    ctx_[sid].enc = std::make_unique<V4LEncoder>(info, chipmunk::kModelW, chipmunk::kModelH, opt);
    ctx_[sid].enc->encoder_open();
  }

  // ---- 配对 / 编码（持 state_mtx）----
  // 配对键 = timestamp_sof 邻近（见 pair_matcher.h：真机实测两路 frame_id 是各自
  // 独立的出帧计数器、持续漂移，不能作配对键）。配对死亡只计数/日志，不上报调度器；
  // road 死亡帧的 SOF 仍参与时间槽编号，wide 死亡帧不参与编号。
  void place_frame(StreamId sid, const VisionIpcBufExtra& extra, VisionBuf* buf, const float mat[9],
                   uint64_t recv_ns, uint64_t warped_ns) {
    std::lock_guard<std::mutex> lk(state_mtx_);
    drain_events_locked();

    const bool is_road = (sid == kRoad);
    Slot slot{extra, buf};
    std::memcpy(slot.mat, mat, sizeof slot.mat);
    slot.recv_ns = recv_ns;
    slot.warped_ns = warped_ns;
    bgm::PairMatcher<Slot>::Frame f{extra.frame_id, extra.timestamp_sof, slot};
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
    // 双路 request 同帧落点 = 本帧双路 IDR = App 侧新序列（bgm1_server.cpp new_seq：
    // 连接首帧 || 中流双路 IDR 对）。MODEL_ABI §4.3：新序列清 previousDesire，
    // 持续中的 desire 在新序列首帧重出 pulse（pulse 跟帧走，帧丢即丢，不重试不补发）。
    const bool new_seq = need_req_road_ && need_req_wide_;
    if (need_req_road_) {
      ctx_[kRoad].enc->request_keyframe();
      need_req_road_ = false;
    }
    if (need_req_wide_) {
      ctx_[kWide].enc->request_keyframe();
      need_req_wide_ = false;
    }
    if (new_seq) meta_.reset_desire_latch();

    update_bitrate_locked();

    bgm1::FrameHeader hdr;
    meta_.fill(road.extra.timestamp_eof, &hdr);
    std::memcpy(hdr.warp_road, road.mat, sizeof hdr.warp_road);
    std::memcpy(hdr.warp_wide, wide.mat, sizeof hdr.warp_wide);
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

    stages_.on_submit(frame_idx, road.extra.timestamp_eof, road.recv_ns, road.warped_ns, nanos_since_boot());
    // 两路同时提交编码；缓冲由 input_done_callback 归还池
    ctx_[kRoad].enc->encode_frame(road.buf, &road.extra);
    ctx_[kWide].enc->encode_frame(wide.buf, &wide.extra);
  }

  void account_pair_drop_locked(bool is_road, const bgm::PairMatcher<Slot>::Frame& dead) {
    if (is_road) {
      const FrameIdx frame_idx = indexer_.index(dead.timestamp_sof);
      LOGW("bigmodeld: 配对缺帧（road）frame_id=%u frame_idx=%u", dead.frame_id, u32(frame_idx));
    } else {
      LOGW("bigmodeld: 配对缺帧（wide）frame_id=%u", dead.frame_id);
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

  // 链路状态串写 Param（06 号；putNonBlocking 异步，future 竞态用锁串行化）
  void link_state_write() {
    std::lock_guard<std::mutex> lk(link_param_mtx_);
    static Params params;
    params.putNonBlocking("BigmodelLinkState", link_.value());
  }
  // 断连/重连中（等 HELLO 判 blip/restart）
  void link_mark_connecting() {
    link_.on_connecting();
    link_state_write();
  }

  // HELLO 的 ver 与本端 kVersion 不符（锁步升级）：写在 fail() 之后，避免被 connecting 覆盖
  void link_mark_version_mismatch() {
    link_.on_version_mismatch();
    link_state_write();
  }

  // REPLY → navStateSP（20 Hz，rlog / UI）。CURVE 只镜像点距离 / 目标速度 / 曲率，不含经纬度。
  void publish_nav_state(const bgm1::Reply& r) {
    const NavStateSnapshot s = nav_state_snapshot_from_reply(r);
    MessageBuilder msg;
    auto evt = msg.initEvent();
    auto ns = evt.initNavStateSP();
    ns.setFrameIndex(s.frame_index);
    ns.setTimestampEofNs(s.timestamp_eof_ns);
    ns.setGpsOk(s.gps_ok);
    ns.setRouteOk(s.route_ok);
    ns.setNavOk(s.nav_ok);
    ns.setRerouting(s.rerouting);
    ns.setHasNavState(s.has_nav_state);
    ns.setHasRoadAttributes(s.has_road_attributes);
    ns.setHasCurveAhead(s.has_curve_ahead);
    ns.setHasNavDebug(s.has_nav_debug);

    if (s.has_nav_state) {
      auto nav = ns.initNavState();
      nav.setStatus(nav_status_from_wire(s.nav_state.status));
      nav.setManeuverType(s.nav_state.maneuver_type);
      nav.setNextManeuverType(s.nav_state.next_maneuver_type);
      nav.setRoundaboutExitIndex(s.nav_state.roundabout_exit_index);
      nav.setDistanceToManeuverM(s.nav_state.distance_to_maneuver_m);
      nav.setRouteRemainingM(s.nav_state.route_remaining_m);
      nav.setRouteRemainingSeconds(s.nav_state.route_remaining_seconds);
      nav.setAgeMs(s.nav_state.age_ms);
      nav.setNavMapFed(s.nav_state.nav_map_fed);
      nav.setNavigationFed(s.nav_state.navigation_fed);
      nav.setOnRoute(s.nav_state.on_route);
      nav.setOffRouteDistanceM(s.nav_state.off_route_distance_m);
      nav.setHeadingDeg(s.nav_state.heading_deg);
    }
    if (s.has_road_attributes) {
      auto road = ns.initRoadAttributes();
      road.setSpeedLimitKph(s.road_attributes.speed_limit_kph);
      road.setSpeedLimitSource(s.road_attributes.speed_limit_source);
      road.setSpeedLimitAheadKph(s.road_attributes.speed_limit_ahead_kph);
      road.setSpeedLimitAheadDistanceM(s.road_attributes.speed_limit_ahead_distance_m);
      road.setRoadClass(s.road_attributes.road_class);
      road.setRamp(s.road_attributes.ramp);
      road.setTunnel(s.road_attributes.tunnel);
      road.setBridge(s.road_attributes.bridge);
      road.setRoundabout(s.road_attributes.roundabout);
      road.setToll(s.road_attributes.toll);
      road.setOneway(s.road_attributes.oneway);
      road.setLaneCount(s.road_attributes.lane_count);
      road.setMatchDistanceM(s.road_attributes.match_distance_m);
      road.setMatchTrusted(s.road_attributes.match_trusted);
      road.setAgeMs(s.road_attributes.age_ms);
    }
    if (s.has_curve_ahead) {
      auto curve = ns.initCurveAhead();
      const uint8_t max_points = uint8_t(bgm1::kCurveMaxPoints);
      const uint8_t count = s.curve_ahead.point_count > max_points ? max_points
                                                                   : s.curve_ahead.point_count;
      curve.setPointCount(count);
      curve.setMaxLateralAccelerationMPerS2(s.curve_ahead.max_lateral_acceleration_m_per_s2);
      curve.setHorizonDistanceM(s.curve_ahead.horizon_distance_m);
      curve.setAgeMs(s.curve_ahead.age_ms);
      curve.setAlongRoute(s.curve_ahead.along_route);
      curve.setRoadLookahead(s.curve_ahead.road_lookahead);
      curve.setSmoothed(s.curve_ahead.smoothed);
      auto points = curve.initPoints(count);
      for (uint8_t i = 0; i < count; i++) {
        auto p = points[i];
        p.setDistanceAlongRouteM(s.curve_ahead.points[i].distance_along_route_m);
        p.setTargetSpeedKph(s.curve_ahead.points[i].target_speed_kph);
        p.setCurvaturePerM(s.curve_ahead.points[i].curvature_per_m);
      }
    }
    if (s.has_nav_debug) {
      auto debug = ns.initNavDebug();
      debug.setRenderDurationUs(s.nav_debug.render_duration_us);
      debug.setMapMatchDurationUs(s.nav_debug.map_match_duration_us);
      debug.setRouteComputeDurationMs(s.nav_debug.route_compute_duration_ms);
      debug.setNavCpuUsagePercent(s.nav_debug.nav_cpu_usage_percent);
    }
    pm_.send("navStateSP", msg);
  }

  // reply 线程一次决策用的「现在」：先取 GNSS 快照，再取时刻（AU-13：时刻不早于快照，
  // 刚到的新 GNSS 样本不会被判成未来时刻而误清空）。
  NavLinkTick nav_link_tick() {
    NavLinkTick tick;
    GnssSample gnss;
    if (meta_.latest_gnss(&gnss)) {
      tick.gnss.seen = true;
      tick.gnss.gnss = gnss;
    }
    tick.now_ns = nanos_since_boot();
    return tick;
  }

  // CURVE_AHEAD（含经纬度）+ MetaProvider 里票 B 已有的最近一条 GNSS → 弯道目标。
  // 决策与序列化在 NavLinkSession / MapTargetWriter（纯逻辑、宿主单测）；reply 线程只把结果放进
  // 本次写入槽，真正的 Params 文件 I/O 由非实时线程 calib_thread 做（P2）。
  // 锁内只 swap 指针（AU-9）：旧值留在局部变量，出锁后释放，实时线程不在锁内分配/释放堆。
  void publish_map_targets() {
    std::optional<std::string> json = link_session_.take_pending();
    if (!json) return;
    {
      std::lock_guard<std::mutex> lk(map_params_mtx_);
      pending_map_target_velocities_.swap(json);
    }
  }

  // calib_thread（非实时）每拍取走待写的弯道目标；没有待写就什么都不做。
  void write_pending_map_targets() {
    std::optional<std::string> pending;
    {
      std::lock_guard<std::mutex> lk(map_params_mtx_);
      pending.swap(pending_map_target_velocities_);
    }
    if (pending) write_mem_param("MapTargetVelocities", *pending);
  }

  // 阻塞 put 只在非实时线程（calib_thread）或所有线程 join 之后的退出路径调用；
  // Params::put 自身线程安全，且这里没有并发写者，故不持锁做 I/O（避免 reply 线程等锁）。
  // AU-9：put 失败要留痕（SCC-map 读不到新目标）；按 key 限流 1 Hz，1 s 刷新会自愈。
  void write_mem_param(const char* key, const std::string& value) {
    if (mem_params_.put(key, value) == 0) return;  // Params::put 成功返回 0
    const uint64_t now_ns = nanos_since_boot();
    uint64_t& last_log_ns = mem_param_error_log_ns_[key];
    if (now_ns - last_log_ns < 1'000'000'000ULL) return;
    last_log_ns = now_ns;
    LOGE("bigmodeld: 写 mem Params %s 失败", key);
  }

  // ---- sender 事件（sender.mtx 锁内回调：只记账 + 推队列 + 日志，不取 state_mtx）----
  void on_uplink_event(const UplinkEventInfo& e) {
    switch (e.ev) {
      case UplinkEvent::kNewConnection:
        LOGW("bigmodeld: 新连接（frame_idx 与 I 帧状态重置）");
        link_mark_connecting();
        break;
      case UplinkEvent::kStall:
        LOGE("bigmodeld: 假死（≥200 ms 无进展）截断重连 frame_idx=%u", u32(e.frame_idx));
        link_mark_connecting();
        break;
      case UplinkEvent::kLinkLost:
        LOGE("bigmodeld: 连接丢失（连续重连失败，持续重试中）");
        link_.on_lost();
        link_state_write();
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
        stages_.on_sent(e.frame_idx, nanos_since_boot());
        break;
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
    stages_.on_encoded(om.frame_idx, nanos_since_boot());
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

    // 段长超协议上限（spec「线协议」= HELLO 的 max_frame，默认 1 MiB，服务端判 BAD_FRAME）：
    // 帧死亡，不进发送路径（丢帧上报 + 新序列 + 序列头门关门，经事件队列按序落状态机）
    const uint32_t max_seg = max_segment_.load(std::memory_order_relaxed);
    if (len > max_seg) {
      LOGE("bigmodeld: %s 段长 %zu 超上限 %u，丢帧 frame_idx=%u",
           sid == kRoad ? "road" : "wide", len, max_seg, u32(om.frame_idx));
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
  // 04 号 C：REPLY 经 msgq 转交 modeld；票 C：NavExt 镜像给 UI / rlog
  PubMaster pm_{{"bigModelReply", "navStateSP"}};
  FrameStageLog stages_;  // C4 本机分段计时（随 REPLY 上报）

  // 票 C：最近一条 REPLY 的 NavExt 摘要（reply 线程独占）；断连路径必须 reset()
  NavLinkSession link_session_;  // AU-1：epoch 跟踪 + NavExt 摘要 + 弯道目标 writer + HELLO 标志
  // reply 线程交付、calib_thread 写出的弯道目标（map_params_mtx_ 保护）
  std::optional<std::string> pending_map_target_velocities_;
  Params mem_params_{"/dev/shm/params"};  // SCC-map 读的 mem Params（与 Python Params 同一路径）
  std::mutex map_params_mtx_;             // 只保护 pending_map_target_velocities_（不包 I/O）
  std::map<std::string, uint64_t> mem_param_error_log_ns_;  // write_mem_param 失败日志限流（calib 线程独占）

  // 06 号：链路状态（HELLO instance_id 对比 → blip/restart）→ Param "BigmodelLinkState"（07 号读）
  LinkStateTracker link_;
  std::atomic<uint32_t> max_segment_{bgm1::kDefaultMaxSegment};  // 段长上限（HELLO 一到即更新）
  std::mutex link_param_mtx_;     // 串行化 Param 写（putNonBlocking 的 future 非线程安全）

  std::mutex state_mtx_;
  FrameScheduler sched_;
  FrameIndexer indexer_;
  bgm::PairMatcher<Slot> matcher_;

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
  fprintf(stderr,
          "usage: bigmodeld [--host HOST|auto] [--port PORT] [--bitrate BPS]\n"
          "  --host 缺省读 Params \"BigmodelServerHost\"（空 = mDNS 自动发现，限 wlan0 子网）\n");
}

}  // namespace

int main(int argc, char* argv[]) {
  std::string host;  // --host：手动 IP / "auto"；缺省走 Params 与自动发现（06 号）
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

  // 06 号连接目标：--host 显式给定优先（adb reverse/联调用）；否则读 Params
  // "BigmodelServerHost"（07 号设置页写）：非空 = 手动 IP，空 = 自动发现（限 wlan0 子网）。
  std::string fixed = host;
  if (fixed.empty()) {
    Params params;
    fixed = params.get("BigmodelServerHost");
    while (!fixed.empty() && fixed.back() == '\n') fixed.pop_back();
  }
  if (fixed == "auto") fixed.clear();

  std::unique_ptr<ServerLocator> loc;
  if (!fixed.empty()) {
    ServerEndpoint ep;
    ep.host = fixed;
    ep.port = uint16_t(port);
    loc = std::make_unique<FixedLocator>(ep);
    LOGW("bigmodeld: start host=%s port=%d bitrate=%d（手动）", fixed.c_str(), port, bitrate);
  } else {
    // 发现范围每次现探测（Wi-Fi 断开/换网后范围跟着变）
    loc = std::make_unique<AvahiLocator>([](ServerEndpoint* out) {
      SubnetScope s;
      if (!detect_wifi_scope(&s)) return false;  // Wi-Fi 没起：发现不到，持续重试
      return browse_avahi(out, s);
    });
    LOGW("bigmodeld: start host=auto（mDNS 限 wlan0 子网）port=%d bitrate=%d", port, bitrate);
  }

  Bigmodeld bg(loc.get(), bitrate);
  // N4：起任何线程之前由主线程同步清一次（崩溃/SIGKILL 重启后不留上一趟的弯道点与位置）。
  bg.cleanup_map_params();
  std::vector<std::thread> ts;
  ts.emplace_back(&Bigmodeld::capture_thread, &bg, kRoad);
  ts.emplace_back(&Bigmodeld::capture_thread, &bg, kWide);
  ts.emplace_back(&Bigmodeld::writer_thread, &bg);
  ts.emplace_back(&Bigmodeld::reply_thread, &bg);
  ts.emplace_back(&Bigmodeld::calib_thread, &bg);
  for (auto& t : ts) t.join();

  // 退出清理：阻塞写（S1a）。所有线程已停，I/O 不再影响回包/实时线程。
  bg.cleanup_map_params();

  LOGW("bigmodeld: exit");
  return 0;
}
