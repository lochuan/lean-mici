// 宿主单测（16 号）：纯逻辑模块全转移 + 发送器（假 socket/假时钟）+ warp golden + 编解码往返。
// 无设备依赖，Mac/Linux 直接编译（README「宿主测试」）：
//   clang++ -std=c++17 -O1 test_bigmodeld.cc frame_codec.cpp frame_meta.cpp \
//           frame_scheduler.cpp uplink_sender.cpp -o /tmp/test_bigmodeld && /tmp/test_bigmodeld

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "frame_codec.h"
#include "frame_meta.h"
#include "frame_scheduler.h"
#include "uplink_sender.h"

// ---- 极简测试框架 ----
static int g_checks = 0;
static int g_fails = 0;

#define CHECK(cond)                                              \
  do {                                                           \
    g_checks++;                                                  \
    if (!(cond)) {                                               \
      g_fails++;                                                 \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);     \
    }                                                            \
  } while (0)

#define CHECK_NEAR(a, b, tol)                                                    \
  do {                                                                           \
    g_checks++;                                                                  \
    double _a = (a), _b = (b), _t = (tol);                                       \
    if (!(std::fabs(_a - _b) <= _t)) {                                           \
      g_fails++;                                                                 \
      printf("FAIL %s:%d: |%g - %g| > %g\n", __FILE__, __LINE__, _a, _b, _t);    \
    }                                                                            \
  } while (0)

// =====================================================================
// FrameScheduler：I 帧策略 / 丢帧 / 空洞
// =====================================================================

// 连接起始 + GOP20 基准 + wide 晚 10 帧（road 0,20,40,…；wide 0,10,30,50,…）
static void test_sched_gop() {
  FrameScheduler s;
  SchedStep c = s.on_connect();
  CHECK(c.request_keyframe_road && c.request_keyframe_wide);
  CHECK(!c.drop_detected);

  // 新序列第 0 帧 = request 落点：两路 IDR
  SchedStep s0 = s.on_frame_submit(0);
  CHECK(s0.road_idr && s0.wide_idr);
  CHECK(!s0.request_keyframe_road && !s0.request_keyframe_wide && !s0.drop_detected);

  for (uint32_t i = 1; i <= 9; i++) {
    SchedStep x = s.on_frame_submit(i);
    CHECK(!x.any());
  }

  // 第 10 帧：wide 补 I（本帧发出 request、本帧即 IDR）
  SchedStep s10 = s.on_frame_submit(10);
  CHECK(s10.request_keyframe_wide && s10.wide_idr && !s10.road_idr);

  for (uint32_t i = 11; i <= 19; i++) CHECK(!s.on_frame_submit(i).any());

  // road GOP 20
  SchedStep s20 = s.on_frame_submit(20);
  CHECK(s20.road_idr && !s20.wide_idr && !s20.request_keyframe_wide);

  for (uint32_t i = 21; i <= 29; i++) CHECK(!s.on_frame_submit(i).any());

  // wide 基准 30（0,10,30,50,…）
  SchedStep s30 = s.on_frame_submit(30);
  CHECK(s30.wide_idr && !s30.road_idr && !s30.request_keyframe_wide);

  for (uint32_t i = 31; i <= 39; i++) CHECK(!s.on_frame_submit(i).any());

  SchedStep s40 = s.on_frame_submit(40);
  CHECK(s40.road_idr && !s40.wide_idr);
  for (uint32_t i = 41; i <= 49; i++) CHECK(!s.on_frame_submit(i).any());

  SchedStep s50 = s.on_frame_submit(50);
  CHECK(s50.wide_idr && !s50.road_idr);
}

// 显式丢帧 → 新序列（seq 归零、两路 request、再走相位）
static void test_sched_drop() {
  FrameScheduler s;
  s.on_connect();
  s.on_frame_submit(0);
  s.on_frame_submit(1);

  SchedStep d = s.on_frame_dropped(2);
  CHECK(d.drop_detected && d.request_keyframe_road && d.request_keyframe_wide);

  // 落点 = 事件后第一个提交帧（新序列第 0 帧）
  SchedStep s3 = s.on_frame_submit(3);
  CHECK(s3.road_idr && s3.wide_idr && !s3.drop_detected);
  for (uint32_t i = 4; i <= 12; i++) CHECK(!s.on_frame_submit(i).any());
  SchedStep s13 = s.on_frame_submit(13);  // 新序列第 10 帧
  CHECK(s13.request_keyframe_wide && s13.wide_idr);
  for (uint32_t i = 14; i <= 22; i++) CHECK(!s.on_frame_submit(i).any());
  SchedStep s23 = s.on_frame_submit(23);  // 新序列第 20 帧
  CHECK(s23.road_idr && !s23.wide_idr);
}

// 提交序空洞兜底（无人显式上报的死亡帧）→ 同样新序列，落点=洞后首帧
static void test_sched_submit_gap() {
  FrameScheduler s;
  s.on_connect();
  s.on_frame_submit(0);
  s.on_frame_submit(1);

  SchedStep g = s.on_frame_submit(5);
  CHECK(g.drop_detected && g.request_keyframe_road && g.request_keyframe_wide);
  CHECK(g.road_idr && g.wide_idr);  // 洞后首帧 = 新序列第 0 帧

  for (uint32_t i = 6; i <= 14; i++) CHECK(!s.on_frame_submit(i).any());
  SchedStep s15 = s.on_frame_submit(15);
  CHECK(s15.request_keyframe_wide && s15.wide_idr);
}

// 实际发出序空洞 = 丢帧（兜底）；显式上报后 have_last_sent_ 复位不重复触发
static void test_sched_sent_hole() {
  FrameScheduler s;
  s.on_connect();

  CHECK(!s.on_frame_sent(0).any());   // 新序列第一发出帧只做锚点
  CHECK(!s.on_frame_sent(1).any());
  SchedStep h = s.on_frame_sent(3);   // 空洞
  CHECK(h.drop_detected && h.request_keyframe_road && h.request_keyframe_wide);
  CHECK(!s.on_frame_sent(4).any());   // 空洞后重锚
  SchedStep h2 = s.on_frame_sent(6);  // 后续无显式上报的空洞照常上报（帧 5 死亡）
  CHECK(h2.drop_detected && h2.request_keyframe_road && h2.request_keyframe_wide);
}

static void test_sched_sent_mute_after_explicit() {
  FrameScheduler s;
  s.on_connect();
  CHECK(!s.on_frame_sent(0).any());

  // 显式丢帧上报 → have_last_sent_ 复位 → 下一次空洞不再重复触发
  CHECK(s.on_frame_dropped(1).drop_detected);
  CHECK(!s.on_frame_sent(5).any());
  CHECK(!s.on_frame_sent(6).any());
  SchedStep h = s.on_frame_sent(9);   // 新锚点之后的空洞照常
  CHECK(h.drop_detected);
}

// =====================================================================
// UplinkSender：假 socket / 假时钟
// =====================================================================

struct FakeSock : public UplinkSocket {
  enum Mode { kWrite, kBlock, kErr };
  bool connect_ok = true;
  int connect_calls = 0;
  Mode mode = kWrite;
  size_t max_write = (size_t)-1;  // 每次 write_some 最多写多少（模拟部分写）
  std::vector<uint8_t> written;
  bool closed = false;

  bool connect(int timeout_ms) override {
    (void)timeout_ms;
    connect_calls++;
    closed = false;
    return connect_ok;
  }
  int write_some(const uint8_t* data, size_t len) override {
    if (mode == kBlock) return 0;
    if (mode == kErr) return -1;
    size_t n = std::min(len, max_write);
    written.insert(written.end(), data, data + n);
    return (int)n;
  }
  int read_some(uint8_t* data, size_t len) override {
    (void)data;
    (void)len;
    return 0;
  }
  void close() override { closed = true; }
};

struct Ev {
  UplinkEvent ev;
  uint32_t idx;
};

static bgm1::FrameHeader test_hdr() {
  bgm1::FrameHeader h;
  h.t_eof = 123456789ULL;
  h.desire[0] = 0.5f;
  h.traffic_convention[0] = 1.f;
  h.action_t[0] = 0.1f;
  for (int i = 0; i < 9; i++) {
    h.warp_road[i] = (float)(i + 1);
    h.warp_wide[i] = (float)(10 + i);
  }
  return h;
}

// 出包即发：submit_road 后 chunk1（头+road）即可发完；wide 到齐才发 chunk2 报 kFrameSent
static void test_sender_road_out_goes() {
  FakeSock sock;
  uint64_t now = 1000;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });

  CHECK(s.step());  // 建连
  CHECK(s.connected() && sock.connect_calls == 1);
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kNewConnection);

  const uint8_t road[7] = {1, 2, 3, 4, 5, 6, 7};
  const uint8_t wide[5] = {9, 8, 7, 6, 5};
  s.submit_road(0, test_hdr(), road, sizeof road, false, false);
  CHECK(s.step());
  // road 出包即发：chunk1（160 B 头 + 7 B road）已写完，未报 kFrameSent（wide 缺）
  CHECK(sock.written.size() == bgm1::kFrameHdrSize + sizeof road);
  CHECK(evs.size() == 1);

  s.submit_wide(0, wide, sizeof wide, false);
  CHECK(s.step());
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 0);
  CHECK(sock.written.size() == bgm1::frame_wire_size(sizeof road, sizeof wide));

  // 字节布局：头 ‖ road ‖ wide_len ‖ wide ‖ MAC(零)
  bgm1::FrameView fv;
  CHECK(bgm1::parse_frame(sock.written.data(), sock.written.size(), &fv) == bgm1::Err::kOk);
  CHECK(fv.hdr.frame_idx == 0 && fv.hdr.road_len == sizeof road && fv.hdr.wide_len == sizeof wide);
  CHECK(fv.hdr.t_eof == 123456789ULL);
  CHECK_NEAR(fv.hdr.desire[0], 0.5, 1e-6);
  CHECK_NEAR(fv.hdr.warp_road[8], 9.0, 1e-6);
  CHECK(std::memcmp(fv.road, road, sizeof road) == 0);
  CHECK(std::memcmp(fv.wide, wide, sizeof wide) == 0);
  for (size_t i = sock.written.size() - bgm1::kMacSize; i < sock.written.size(); i++) {
    CHECK(sock.written[i] == 0);
  }
}

// flags：bit0 = road 实际 keyframe 位（精确）、bit1 = wide IDR 预测
static void test_sender_flags() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  s.step();

  const uint8_t p[3] = {1, 2, 3};
  s.submit_road(7, test_hdr(), p, sizeof p, true, true);  // 实际 IDR + 预测 wide IDR
  s.submit_wide(7, p, sizeof p, true);                    // 预测相符
  s.step();
  s.step();

  bgm1::FrameHeader h;
  CHECK(bgm1::parse_frame_header(sock.written.data(), &h) == bgm1::Err::kOk);
  CHECK(h.flags == (bgm1::kFlagRoadIdr | bgm1::kFlagWideIdr));
  CHECK(h.frame_idx == 7);
  CHECK(s.wide_idr_mismatches() == 0);

  // 预测 1 实际 0（误报风险）→ 计数
  s.submit_road(8, test_hdr(), p, sizeof p, false, true);
  s.submit_wide(8, p, sizeof p, false);
  CHECK(s.wide_idr_mismatches() == 1);
  // 预测 0 实际 1（欠报无害）→ 也计数（口径：不符即计）
  s.submit_road(9, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(9, p, sizeof p, true);
  CHECK(s.wide_idr_mismatches() == 2);
}

// 在途槽 + 排队槽：排队只留最新，新包覆盖 = kDrop 丢旧包；迟到旧包静默丢
static void test_sender_queue_overwrite() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  s.step();
  evs.clear();

  sock.mode = FakeSock::kBlock;  // 写不动 → 帧 0 停在在途
  const uint8_t p[2] = {0xaa, 0xbb};
  s.submit_road(0, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(0, p, sizeof p, false);
  s.step();  // queued → inflight，写阻塞

  s.submit_road(1, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(1, p, sizeof p, false);
  s.step();  // 进排队槽
  CHECK(evs.empty());

  // 新包覆盖排队槽 = 丢弃帧 1（kDrop）
  s.submit_road(2, test_hdr(), p, sizeof p, false, false);
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kDrop && evs[0].idx == 1);

  // 帧 1 的迟到包（如 wide）静默丢弃，不覆盖帧 2
  s.submit_wide(1, p, sizeof p, false);
  s.submit_wide(2, p, sizeof p, false);
  CHECK(evs.size() == 1);

  // 通了以后发的是帧 0、帧 2
  sock.mode = FakeSock::kWrite;
  s.step();
  s.step();
  CHECK(evs.size() == 3);
  CHECK(evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 0);
  CHECK(evs[2].ev == UplinkEvent::kFrameSent && evs[2].idx == 2);
}

// 假死（≥200 ms 无进展）：上报 kStall，写得动就发完在途帧再重连
static void test_sender_stall_finish() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) {
                   evs.push_back({e.ev, e.frame_idx});
                   if (e.ev == UplinkEvent::kStall) {
                     // 假死时刻突然可写、但只能短写：补发必须连续推进直到发完（不撕裂）
                     sock.mode = FakeSock::kWrite;
                     sock.max_write = 3;
                   }
                 });
  s.step();
  evs.clear();

  sock.mode = FakeSock::kBlock;
  const uint8_t p[2] = {1, 2};
  s.submit_road(0, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(0, p, sizeof p, false);
  s.step();
  CHECK(evs.empty());

  now += 199;
  s.step();  // 未到阈值
  CHECK(evs.empty());

  now += 1;
  s.step();  // 达阈值：kStall → 尝试发完（可写 → 完整发出）→ 断开
  CHECK(evs.size() == 2);
  CHECK(evs[0].ev == UplinkEvent::kStall && evs[0].idx == 0);
  CHECK(evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 0);
  CHECK(!s.connected());
  CHECK(sock.closed);

  CHECK(s.step());  // 下一次 step 重连
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kNewConnection);
}

// 假死且写不动：截断（不发 kFrameSent），重连清空
static void test_sender_stall_truncate() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  s.step();
  evs.clear();

  sock.mode = FakeSock::kBlock;
  const uint8_t p[2] = {1, 2};
  s.submit_road(0, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(0, p, sizeof p, false);
  s.step();
  now += 200;
  s.step();  // kStall → 截断 → 重连（sock 仍 block）
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kStall);

  sock.mode = FakeSock::kWrite;
  CHECK(s.step());  // 重连成功 → kNewConnection；旧帧不带进新连接
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kNewConnection);

  const size_t before = sock.written.size();
  s.step();
  CHECK(sock.written.size() == before);  // 队列已清空，无残帧可发
}

// 重连：每次 connect 1 s 超时；连败 3 次升级 kLinkLost；此后持续重试、成功复位
static void test_sender_reconnect_linklost() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  s.step();
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kNewConnection);

  // 读侧发现断连 → 重连路径
  s.notify_disconnect();
  CHECK(!s.connected());

  sock.connect_ok = false;
  CHECK(!s.step());
  CHECK(!s.step());
  CHECK(evs.size() == 1 && s.connect_failures() == 2);
  CHECK(!s.step());  // 第 3 次失败 → 升级「连接丢失」
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kLinkLost);
  CHECK(s.connect_failures() == 3);

  CHECK(!s.step());  // 之后继续重试，不重复升级
  CHECK(evs.size() == 2);

  sock.connect_ok = true;
  CHECK(s.step());
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kNewConnection);
  CHECK(s.connect_failures() == 0 && s.connected());
}

// 断连/未连接：submit_* 静默丢（不阻塞编码回调），队列不带进新连接
static void test_sender_submit_while_down() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  const uint8_t p[2] = {1, 2};

  s.submit_road(0, test_hdr(), p, sizeof p, false, false);  // 未连接 → 丢
  s.submit_wide(0, p, sizeof p, false);
  CHECK(evs.empty());
  CHECK(s.step());
  s.step();
  CHECK(evs.size() == 1);  // 只有 kNewConnection，无 kFrameSent

  // 建连后入队一帧，再断连 → 旧帧不发
  sock.mode = FakeSock::kBlock;
  s.submit_road(1, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(1, p, sizeof p, false);
  s.notify_disconnect();
  CHECK(!s.connected());
  sock.mode = FakeSock::kWrite;
  CHECK(s.step());
  s.step();
  CHECK(evs.size() == 2);  // kNewConnection ×2，帧 1 已随断连清空
}

// 部分写不撕裂：chunk1/chunk2 分多次 write_some 拼完整
static void test_sender_partial_writes() {
  FakeSock sock;
  sock.max_write = 3;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  s.step();

  const uint8_t road[10] = {0};
  const uint8_t wide[7] = {1};
  s.submit_road(0, test_hdr(), road, sizeof road, false, false);
  s.submit_wide(0, wide, sizeof wide, false);
  for (int i = 0; i < 100 && evs.size() < 2; i++) {
    now++;
    s.step();  // 假时钟推进避免中途判假死
  }
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 0);
  CHECK(sock.written.size() == bgm1::frame_wire_size(sizeof road, sizeof wide));
}

// 写错误 → 重连
static void test_sender_write_error() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, e.frame_idx}); });
  s.step();

  sock.mode = FakeSock::kErr;
  const uint8_t p[2] = {1, 2};
  s.submit_road(0, test_hdr(), p, sizeof p, false, false);
  s.submit_wide(0, p, sizeof p, false);
  s.step();
  CHECK(!s.connected());
  sock.mode = FakeSock::kWrite;
  CHECK(s.step());
  CHECK(s.connected());
}

// =====================================================================
// ReplyTracker：REPLY/ERR 记账
// =====================================================================

static void test_reply_tracker() {
  ReplyTracker t;
  CHECK(!t.has_reply());

  bgm1::Reply r;
  r.frame_idx = 5;
  r.t_eof = 42;
  r.flags = bgm1::kFlagSeqReset;
  r.outputs[0] = 1.5f;
  r.outputs[bgm1::kReplyOutputsCount - 1] = -2.f;
  r.telemetry[0] = 100;
  r.telemetry[1] = 200;
  r.telemetry[2] = 300;
  r.telemetry[3] = 600;
  t.on_reply(r);

  r.telemetry[0] = 300;
  r.telemetry[3] = 1000;
  r.flags = bgm1::kFlagZeroPair;
  t.on_reply(r);

  CHECK(t.replies() == 2);
  CHECK(t.seq_reset() == 1 && t.zero_pair() == 1);
  CHECK(t.has_reply() && t.latest().frame_idx == 5 && t.latest().t_eof == 42);
  CHECK_NEAR(t.latest().outputs[0], 1.5, 1e-6);
  CHECK_NEAR(t.latest().outputs[bgm1::kReplyOutputsCount - 1], -2.0, 1e-6);

  const ReplyTracker::SegmentStats& s0 = t.segment(0);
  CHECK(s0.count == 2 && s0.min_us == 100 && s0.max_us == 300);
  CHECK_NEAR(s0.mean_us(), 200.0, 1e-9);
  const ReplyTracker::SegmentStats& s3 = t.segment(3);
  CHECK(s3.count == 2 && s3.min_us == 600 && s3.max_us == 1000);
  CHECK_NEAR(s3.mean_us(), 800.0, 1e-9);
  CHECK(std::string(ReplyTracker::segment_name(0)) == "srv_recv_us");
  CHECK(std::string(ReplyTracker::segment_name(3)) == "srv_total_us");

  CHECK(!t.has_err());
  bgm1::ErrMsg e;
  e.code = bgm1::kErrPairMismatch;
  e.detail = 9;
  e.frame_idx = 3;
  t.on_err(e);
  CHECK(t.has_err() && t.last_err().code == bgm1::kErrPairMismatch && t.last_err().detail == 9);
  t.on_disconnect();  // 统计保留
  CHECK(t.replies() == 2 && t.has_reply());
}

// =====================================================================
// frame_codec：往返 + 坏输入（冒烟）
// =====================================================================

static void test_frame_codec_roundtrip() {
  bgm1::FrameHeader h = test_hdr();
  h.flags = bgm1::kFlagRoadIdr;
  h.frame_idx = 33;
  h.road_len = 11;
  h.wide_len = 5;
  const uint8_t road[11] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
  const uint8_t wide[5] = {9, 9, 9, 9, 9};

  std::vector<uint8_t> buf(bgm1::frame_wire_size(h.road_len, h.wide_len));
  CHECK(bgm1::pack_frame(h, road, wide, buf.data(), buf.size()) == buf.size());
  CHECK(bgm1::pack_frame(h, road, wide, buf.data(), buf.size() - 1) == 0);  // cap 不足

  bgm1::FrameView fv;
  CHECK(bgm1::parse_frame(buf.data(), buf.size(), &fv) == bgm1::Err::kOk);
  CHECK(fv.hdr.flags == bgm1::kFlagRoadIdr && fv.hdr.road_idr() && !fv.hdr.wide_idr());
  CHECK(fv.hdr.frame_idx == 33 && fv.hdr.road_len == 11 && fv.hdr.wide_len == 5);
  CHECK(fv.hdr.t_eof == h.t_eof);
  for (int i = 0; i < 8; i++) CHECK_NEAR(fv.hdr.desire[i], h.desire[i], 1e-6);
  for (int i = 0; i < 9; i++) {
    CHECK_NEAR(fv.hdr.warp_road[i], h.warp_road[i], 1e-6);
    CHECK_NEAR(fv.hdr.warp_wide[i], h.warp_wide[i], 1e-6);
  }
  CHECK(std::memcmp(fv.road, road, sizeof road) == 0);
  CHECK(std::memcmp(fv.wide, wide, sizeof wide) == 0);

  // 坏输入
  std::vector<uint8_t> bad = buf;
  bad[0] ^= 0xff;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kBadMagic);
  bad = buf;
  bad[4] = 9;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kBadVersion);
  bad = buf;
  bad[5] = bgm1::kTypeReply;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kBadType);
  CHECK(bgm1::parse_frame(buf.data(), buf.size() - 1, &fv) == bgm1::Err::kLenMismatch);
  CHECK(bgm1::parse_frame(buf.data(), bgm1::kFrameMinSize - 1, &fv) == bgm1::Err::kTruncated);
  // road_len 超上限 → kLenTooLarge（不是截断）
  bad = buf;
  bad[12] = 0xff; bad[13] = 0xff; bad[14] = 0xff; bad[15] = 0xff;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kLenTooLarge);
}

static void test_reply_err_roundtrip() {
  bgm1::Reply r;
  r.flags = bgm1::kFlagSeqReset | bgm1::kFlagZeroPair;
  r.frame_idx = 77;
  r.t_eof = 987654321ULL;
  for (size_t i = 0; i < bgm1::kReplyOutputsCount; i++) r.outputs[i] = (float)i * 0.25f;
  for (size_t i = 0; i < bgm1::kReplyTelemetryCount; i++) r.telemetry[i] = 1000 * (uint32_t)(i + 1);

  std::vector<uint8_t> buf(bgm1::kReplyWireSize);
  CHECK(bgm1::pack_reply(r, buf.data(), buf.size()) == bgm1::kReplyWireSize);
  bgm1::Reply out;
  CHECK(bgm1::parse_reply(buf.data(), buf.size(), &out) == bgm1::Err::kOk);
  CHECK(out.flags == r.flags && out.frame_idx == 77 && out.t_eof == r.t_eof);
  CHECK(out.seq_reset() && out.zero_pair());
  bool outputs_ok = true;
  for (size_t i = 0; i < bgm1::kReplyOutputsCount; i++) outputs_ok &= (out.outputs[i] == r.outputs[i]);
  CHECK(outputs_ok);
  for (size_t i = 0; i < bgm1::kReplyTelemetryCount; i++) CHECK(out.telemetry[i] == r.telemetry[i]);

  CHECK(bgm1::parse_reply(buf.data(), buf.size() - 1, &out) == bgm1::Err::kTruncated);
  std::vector<uint8_t> bad = buf;
  bad[5] = bgm1::kTypeFrame;
  CHECK(bgm1::parse_reply(bad.data(), bad.size(), &out) == bgm1::Err::kBadType);

  bgm1::ErrMsg e;
  e.code = bgm1::kErrVersionMismatch;
  e.detail = 2;
  e.frame_idx = 13;
  std::vector<uint8_t> ebuf(bgm1::kErrWireSize);
  CHECK(bgm1::pack_err(e, ebuf.data(), ebuf.size()) == bgm1::kErrWireSize);
  bgm1::ErrMsg eout;
  CHECK(bgm1::parse_err(ebuf.data(), ebuf.size(), &eout) == bgm1::Err::kOk);
  CHECK(eout.code == bgm1::kErrVersionMismatch && eout.detail == 2 && eout.frame_idx == 13);
  CHECK(bgm1::parse_err(ebuf.data(), ebuf.size() - 1, &eout) == bgm1::Err::kTruncated);
}

// =====================================================================
// warp golden（gen_warp_golden.py 生成）+ MetaProvider
// =====================================================================

// gen_warp_golden.py 生成（勿手改）：rpy 样本 + get_warp_matrix 的 warp_road/warp_wide
// 数值口径：rpy 按 float32 取值（与 extrinsicsCalibration.rpyCalib 同）后以 double 精度
// 算同一公式（rot_from_euler + matmul + inv），与 frame_meta.cpp 的 double 实现对得上；
// modeld 实际路径的 float32 中间精度另带 ~3e-5 绝对噪声（相对 ~1e-7），不进本判据。
// 内参（DEVICE_CAMERAS[('mici','os04c10')]）：narrow fl=1141.5 wide fl=425.25 size=(1344, 760)
struct WarpGolden { float rpy[3]; float road[9]; float wide[9]; };
static const WarpGolden kWarpGolden[] = {
  {{0.0f, 0.0f, 0.0f}, {1.2543956f, 1.63676136e-15f, 350.874725f, 0.0f, 1.2543956f, 320.290771f, 0.0f, 4.66480276e-18f, 1.0f}, {0.934615374f, 0.0f, 432.738464f, 0.0f, 0.934615374f, 238.125381f, 0.0f, 0.0f, 1.0f}},
  {{0.00999999978f, -0.0199999996f, 0.00499999989f}, {1.25047612f, -0.0273994152f, 358.74585f, 0.0103699304f, 1.24575233f, 340.795227f, -5.71396686e-06f, -2.19202393e-05f, 1.00229371f}, {0.926876485f, -0.0389001332f, 442.607697f, 0.00500151375f, 0.917722344f, 247.833038f, -1.14279337e-05f, -4.38404786e-05f, 1.00936806f}},
  {{-0.0299999993f, 0.0399999991f, -0.00999999978f}, {1.26027894f, 0.0668602735f, 334.209259f, -0.0339231156f, 1.26964402f, 282.278503f, 9.66581683e-06f, 4.42519668e-05f, 0.994569302f}, {0.947150171f, 0.0871339291f, 411.482483f, -0.0206658095f, 0.967079103f, 221.159439f, 1.93316337e-05f, 8.85039335e-05f, 0.980766356f}},
  {{0.0500000007f, 0.0500000007f, 0.0500000007f}, {1.21639955f, -0.0208257213f, 416.894348f, 0.0428127423f, 1.27312362f, 250.438828f, -5.2111991e-05f, 5.75299382e-05f, 1.00810432f}, {0.862358928f, 0.0329989865f, 465.77533f, 0.00704780966f, 0.976003528f, 207.835571f, -0.000104223982f, 0.000115059876f, 1.00671732f}},
  {{-0.100000001f, 0.0f, 0.100000001f}, {1.16853857f, 0.117244937f, 477.875916f, -0.166711017f, 1.24396694f, 361.566772f, -0.000109158973f, -1.09524299e-05f, 1.02347016f}, {0.778590679f, 0.078119643f, 499.919189f, -0.17626667f, 0.921622336f, 283.323578f, -0.000218317946f, -2.19048598e-05f, 1.05421877f}},
  {{0.0f, -0.150000006f, 0.0f}, {1.2543956f, -0.110354319f, 348.581757f, 0.0f, 1.17790735f, 490.24826f, 0.0f, -0.000164217738f, 0.996587813f}, {0.934615374f, -0.220708638f, 458.696198f, 0.0f, 0.799315155f, 317.945526f, 0.0f, -0.000328435475f, 1.03862762f}},
  {{0.200000003f, -0.200000003f, 0.300000012f}, {0.918125808f, -0.404265493f, 744.004028f, 0.107552513f, 1.15172613f, 500.216919f, -0.000359710044f, -0.000139892276f, 1.03503799f}, {0.380722493f, -0.419179767f, 718.52063f, -0.0914014503f, 0.79140842f, 343.538574f, -0.000719420088f, -0.000279784552f, 1.16293621f}},
  {{9.99999975e-05f, 0.000199999995f, -0.000300000014f}, {1.25461709f, 2.21553273e-05f, 350.474487f, 0.000250722631f, 1.25447905f, 319.994293f, 3.29692313e-07f, 2.19747236e-07f, 0.99990505f}, {0.935058415f, 0.000201822681f, 432.466797f, 0.000344027707f, 0.934782386f, 237.926895f, 6.59384625e-07f, 4.39494471e-07f, 0.999764442f}},
};
// 内参核对（与 frame_meta.cpp 对读）：
//   kNarrowRoadIntrinsics[9] = {1141.5f, 0.0f, 672.0f, 0.0f, 1141.5f, 380.0f, 0.0f, 0.0f, 1.0f}
//   kWideRoadIntrinsics[9] = {425.25f, 0.0f, 672.0f, 0.0f, 425.25f, 380.0f, 0.0f, 0.0f, 1.0f}

static void test_warp_golden() {
  // 内参常量与 camera.py _os_config（os04c10）逐位一致
  const float narrow[9] = {1141.5f, 0.f, 672.f, 0.f, 1141.5f, 380.f, 0.f, 0.f, 1.f};
  const float wide[9] = {425.25f, 0.f, 672.f, 0.f, 425.25f, 380.f, 0.f, 0.f, 1.f};
  for (int i = 0; i < 9; i++) {
    CHECK(kNarrowRoadIntrinsics[i] == narrow[i]);
    CHECK(kWideRoadIntrinsics[i] == wide[i]);
  }

  // rpy=0 标定性抽查：road[0] = fl_narrow/910、road[2] = cx − road[0]·256
  CHECK_NEAR(kWarpGolden[0].road[0], 1141.5 / 910.0, 1e-6);
  CHECK_NEAR(kWarpGolden[0].road[2], 672.0 - (1141.5 / 910.0) * 256.0, 1e-3);

  for (const WarpGolden& g : kWarpGolden) {
    float road[9], wwide[9];
    get_warp_matrix(g.rpy, kNarrowRoadIntrinsics, false, road);
    get_warp_matrix(g.rpy, kWideRoadIntrinsics, true, wwide);
    for (int i = 0; i < 9; i++) {
      CHECK_NEAR(road[i], g.road[i], 1e-5);
      CHECK_NEAR(wwide[i], g.wide[i], 1e-5);
    }
  }
}

static void test_meta_provider() {
  MetaProvider mp;
  bgm1::FrameHeader h;
  std::memset(&h, 0x5a, sizeof h);  // 脏内存，验证 fill 全覆盖
  mp.fill(42, &h);

  CHECK(h.t_eof == 42);
  CHECK(h.traffic_convention[0] == 1.f && h.traffic_convention[1] == 0.f);
  for (int i = 0; i < 8; i++) CHECK(h.desire[i] == 0.f);
  CHECK(h.action_t[0] == 0.f && h.action_t[1] == 0.f);
  // 默认 rpy=0 → warp 与 golden 首样本一致
  for (int i = 0; i < 9; i++) {
    CHECK_NEAR(h.warp_road[i], kWarpGolden[0].road[i], 1e-5);
    CHECK_NEAR(h.warp_wide[i], kWarpGolden[0].wide[i], 1e-5);
  }

  // 标定更新 → warp 跟随
  const WarpGolden& g = kWarpGolden[3];
  mp.set_rpy(g.rpy);
  mp.fill(43, &h);
  for (int i = 0; i < 9; i++) {
    CHECK_NEAR(h.warp_road[i], g.road[i], 1e-5);
    CHECK_NEAR(h.warp_wide[i], g.wide[i], 1e-5);
  }
  CHECK(h.t_eof == 43);
}

int main() {
  test_sched_gop();
  test_sched_drop();
  test_sched_submit_gap();
  test_sched_sent_hole();
  test_sched_sent_mute_after_explicit();

  test_sender_road_out_goes();
  test_sender_flags();
  test_sender_queue_overwrite();
  test_sender_stall_finish();
  test_sender_stall_truncate();
  test_sender_reconnect_linklost();
  test_sender_submit_while_down();
  test_sender_partial_writes();
  test_sender_write_error();

  test_reply_tracker();

  test_frame_codec_roundtrip();
  test_reply_err_roundtrip();

  test_warp_golden();
  test_meta_provider();

  printf("%d checks, %d fails\n", g_checks, g_fails);
  return g_fails == 0 ? 0 : 1;
}
