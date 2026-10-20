// 宿主单测（16 号）：纯逻辑模块全转移 + 发送器（假 socket/假时钟）+ warp golden + 编解码往返。
// 无设备依赖，Mac/Linux 直接编译（README「宿主测试」）：
//   clang++ -std=c++17 -O1 test_bigmodeld.cc frame_codec.cpp frame_meta.cpp frame_scheduler.cpp \
//           uplink_sender.cpp server_locator.cpp -o /tmp/test_bigmodeld
//   BGM1_GOLDEN_DIR=<chipmunk-os>/app/tests/fixtures /tmp/test_bigmodeld
// 跨端 golden 互解默认必跑（目录未设置 / golden 缺失或为空都判失败）；显式跳过：BGM1_GOLDEN_SKIP=1

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <string>
#include <thread>
#include <vector>

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

// 全局分配计数：test_meta_fill_zero_alloc 用（MetaProvider::fill 不得堆分配）。
// 覆盖全局 operator new/delete 只计数，行为与默认一致；本测试进程全是纯逻辑模块。
static std::atomic<uint64_t> g_alloc_count{0};
void* operator new(std::size_t n) {
  g_alloc_count.fetch_add(1, std::memory_order_relaxed);
  if (void* p = std::malloc(n ? n : 1)) return p;
  throw std::bad_alloc();
}
void* operator new[](std::size_t n) {
  g_alloc_count.fetch_add(1, std::memory_order_relaxed);
  if (void* p = std::malloc(n ? n : 1)) return p;
  throw std::bad_alloc();
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }

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
// FrameScheduler / FrameIndexer：I 帧策略、码流断档恢复与时间槽编号
// =====================================================================

// 连接起始 + GOP20 基准 + wide 晚 10 帧（road 0,20,40,…；wide 0,10,30,50,…）
static void test_sched_gop() {
  FrameScheduler s;
  SchedStep c = s.on_connect();
  CHECK(c.request_keyframe_road && c.request_keyframe_wide);
  CHECK(!c.drop_detected);

  // 新序列第 0 帧 = request 落点：两路 IDR
  SchedStep s0 = s.on_frame_submit(FrameIdx{0});
  CHECK(s0.road_idr && s0.wide_idr);
  CHECK(!s0.request_keyframe_road && !s0.request_keyframe_wide && !s0.drop_detected);

  for (uint32_t i = 1; i <= 9; i++) {
    SchedStep x = s.on_frame_submit(FrameIdx{i});
    CHECK(!x.any());
  }

  // 第 10 帧：wide 补 I（本帧发出 request、本帧即 IDR）
  SchedStep s10 = s.on_frame_submit(FrameIdx{10});
  CHECK(s10.request_keyframe_wide && s10.wide_idr && !s10.road_idr);

  for (uint32_t i = 11; i <= 19; i++) CHECK(!s.on_frame_submit(FrameIdx{i}).any());

  // road GOP 20
  SchedStep s20 = s.on_frame_submit(FrameIdx{20});
  CHECK(s20.road_idr && !s20.wide_idr && !s20.request_keyframe_wide);

  for (uint32_t i = 21; i <= 29; i++) CHECK(!s.on_frame_submit(FrameIdx{i}).any());

  // wide 基准 30（0,10,30,50,…）
  SchedStep s30 = s.on_frame_submit(FrameIdx{30});
  CHECK(s30.wide_idr && !s30.road_idr && !s30.request_keyframe_wide);

  for (uint32_t i = 31; i <= 39; i++) CHECK(!s.on_frame_submit(FrameIdx{i}).any());

  SchedStep s40 = s.on_frame_submit(FrameIdx{40});
  CHECK(s40.road_idr && !s40.wide_idr);
  for (uint32_t i = 41; i <= 49; i++) CHECK(!s.on_frame_submit(FrameIdx{i}).any());

  SchedStep s50 = s.on_frame_submit(FrameIdx{50});
  CHECK(s50.wide_idr && !s50.road_idr);
}

// 发送侧显式丢弃（kDrop）→ 新序列（seq 归零、两路 request、再走相位）
static void test_sched_drop() {
  FrameScheduler s;
  s.on_connect();
  s.on_frame_submit(FrameIdx{0});
  s.on_frame_submit(FrameIdx{1});

  SchedStep d = s.on_frame_dropped(FrameIdx{2});  // 发送侧显式丢弃，不是配对失败
  CHECK(d.drop_detected && d.request_keyframe_road && d.request_keyframe_wide);

  // 落点 = 事件后第一个提交帧（新序列第 0 帧）
  SchedStep s3 = s.on_frame_submit(FrameIdx{3});
  CHECK(s3.road_idr && s3.wide_idr && !s3.drop_detected);
  for (uint32_t i = 4; i <= 12; i++) CHECK(!s.on_frame_submit(FrameIdx{i}).any());
  SchedStep s13 = s.on_frame_submit(FrameIdx{13});  // 新序列第 10 帧
  CHECK(s13.request_keyframe_wide && s13.wide_idr);
  for (uint32_t i = 14; i <= 22; i++) CHECK(!s.on_frame_submit(FrameIdx{i}).any());
  SchedStep s23 = s.on_frame_submit(FrameIdx{23});  // 新序列第 20 帧
  CHECK(s23.road_idr && !s23.wide_idr);
}

// 同一时间槽重复显式丢弃只恢复一次（例如同帧两路编码段都超限）
static void test_sched_drop_dedup() {
  FrameScheduler s;
  s.on_connect();
  s.on_frame_submit(FrameIdx{0});

  SchedStep d1 = s.on_frame_dropped(FrameIdx{2});
  CHECK(d1.drop_detected && d1.request_keyframe_road && d1.request_keyframe_wide);
  SchedStep d2 = s.on_frame_dropped(FrameIdx{2});  // 重复上报：静默
  CHECK(!d2.any());
  SchedStep d3 = s.on_frame_dropped(FrameIdx{3});  // 不同帧照常
  CHECK(d3.drop_detected && d3.request_keyframe_road && d3.request_keyframe_wide);
  SchedStep d4 = s.on_frame_dropped(FrameIdx{3});  // 同一槽第二次上报：同样静默
  CHECK(!d4.any());

  // 重连后 frame_idx 重新编号：去重窗口作废
  s.on_connect();
  SchedStep d5 = s.on_frame_dropped(FrameIdx{3});
  CHECK(d5.drop_detected);
}

// 19 号 #2 回归：编码输出被吞（kStreamGap）无键、不去重——两路 frame_id 同值时
// 两次断档上报都必须生效（旧版 0x80000000u|frame_id 魔法位键会吞掉第二路）
static void test_sched_stream_gap_no_dedup() {
  FrameScheduler s;
  s.on_connect();
  s.on_frame_submit(FrameIdx{0});

  SchedStep g1 = s.on_stream_gap();  // road 输出被吞
  CHECK(g1.drop_detected && g1.request_keyframe_road && g1.request_keyframe_wide);
  SchedStep g2 = s.on_stream_gap();  // wide 同 frame_id 输出同样被吞：不得被去重压掉
  CHECK(g2.drop_detected && g2.request_keyframe_road && g2.request_keyframe_wide);
}

// frame_idx 是时间槽：提交序出现空槽不恢复，GOP 相位仍按实际提交帧数推进
static void test_sched_submit_time_slot_hole() {
  FrameScheduler s;
  s.on_connect();
  const uint32_t slots[] = {0, 1, 3, 5, 6, 8, 9, 11, 12, 13, 15};
  for (size_t i = 0; i < sizeof(slots) / sizeof(slots[0]); i++) {
    SchedStep step = s.on_frame_submit(FrameIdx{slots[i]});
    if (i == 0) {
      CHECK(step.road_idr && step.wide_idr);
    } else if (i == 10) {
      CHECK(step.request_keyframe_wide && step.wide_idr);
    } else {
      CHECK(!step.any());
    }
    CHECK(!step.drop_detected);
  }
}

// 50 ms 一个时间槽；SOF 100 ms 跳跃对应 +2。重连重置时间基准。
static void test_frame_indexer() {
  constexpr uint64_t t0 = 1000000000ULL;
  constexpr uint64_t ms = 1000000ULL;
  FrameIndexer indexer;

  FrameIdx idx = indexer.index(t0);
  CHECK(u32(idx) == 0);
  uint32_t prev = u32(idx);
  idx = indexer.index(t0 + 50 * ms);
  CHECK(u32(idx) == prev + 1);
  prev = u32(idx);
  idx = indexer.index(t0 + 150 * ms);  // 相邻 SOF 相差 100 ms，跨两个槽
  CHECK(u32(idx) == prev + 2);
  prev = u32(idx);
  idx = indexer.index(t0 + 200 * ms);
  CHECK(u32(idx) == prev + 1);

  // 连接重置后，首个被编号 SOF 成为新基准，即使绝对 SOF 已向前很久也从 0 起。
  indexer.reset();
  CHECK(indexer.index(t0 + 10000 * ms) == FrameIdx{0});
  CHECK(indexer.index(t0 + 10050 * ms) == FrameIdx{1});
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
  std::function<void()> on_write;  // 每次成功写出后回调（推进假时钟模拟写入耗时）
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
    if (on_write) on_write();
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
  uint32_t ep;  // kNewConnection 的连接代号
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

// 序列头门下的标准开局：预测 + 实测双路 IDR 头对开门（唯一开流条件）
static void open_head(UplinkSender& s, uint32_t ep, uint32_t idx,
                      const uint8_t* road, size_t road_len,
                      const uint8_t* wide, size_t wide_len) {
  s.submit_road(ConnEpoch{ep}, FrameIdx{idx}, test_hdr(), road, road_len, /*road_actual=*/true,
                /*road_pred=*/true, /*wide_pred=*/true);
  s.submit_wide(ConnEpoch{ep}, FrameIdx{idx}, wide, wide_len, /*wide_actual=*/true);
}

// 出包即发：序列头门放行后（头对 = 预测+实测双路 IDR），中流 road 即可先发 chunk1，
// wide 到齐才发 chunk2 报 kFrameSent
static void test_sender_road_out_goes() {
  FakeSock sock;
  uint64_t now = 1000;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });

  CHECK(s.step());  // 建连
  CHECK(s.connected() && sock.connect_calls == 1);
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kNewConnection && evs[0].ep == 1);

  const uint8_t road[7] = {1, 2, 3, 4, 5, 6, 7};
  const uint8_t wide[5] = {9, 8, 7, 6, 5};

  // 头对（帧 0）：预测 + 实测双路 IDR 开流；两段到齐一次发完
  open_head(s, evs[0].ep, 0, road, sizeof road, wide, sizeof wide);
  CHECK(s.step());
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 0);
  CHECK(sock.written.size() == bgm1::frame_wire_size(sizeof road, sizeof wide));

  // 字节布局：头 ‖ road ‖ wide_len ‖ wide ‖ 尾部保留 16 B(零)
  bgm1::FrameView fv;
  CHECK(bgm1::parse_frame(sock.written.data(), sock.written.size(), &fv) == bgm1::Err::kOk);
  CHECK(fv.hdr.frame_idx == 0 && fv.hdr.road_len == sizeof road && fv.hdr.wide_len == sizeof wide);
  CHECK(fv.hdr.t_eof == 123456789ULL);
  CHECK_NEAR(fv.hdr.desire[0], 0.5, 1e-6);
  CHECK_NEAR(fv.hdr.warp_road[8], 9.0, 1e-6);
  CHECK(std::memcmp(fv.road, road, sizeof road) == 0);
  CHECK(std::memcmp(fv.wide, wide, sizeof wide) == 0);
  for (size_t i = sock.written.size() - bgm1::kTailReservedSize; i < sock.written.size(); i++) {
    CHECK(sock.written[i] == 0);
  }

  // 中流（帧 1）：road 出包即发——chunk1 先走，未报 kFrameSent（wide 缺）
  s.submit_road(ConnEpoch{evs[0].ep}, FrameIdx{1}, test_hdr(), road, sizeof road, false, false, false);
  CHECK(s.step());
  CHECK(sock.written.size() == bgm1::frame_wire_size(sizeof road, sizeof wide) +
                               bgm1::kFrameHdrSize + sizeof road);
  CHECK(evs.size() == 2);

  s.submit_wide(ConnEpoch{evs[0].ep}, FrameIdx{1}, wide, sizeof wide, false);
  CHECK(s.step());
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kFrameSent && evs[2].idx == 1);
}

// flags：bit0 = road 实际 keyframe 位（精确）、bit1 = wide IDR 预测
static void test_sender_flags() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();

  const uint8_t p[3] = {1, 2, 3};
  // 头对（帧 7）：预测 + 实测双路 IDR 开流
  open_head(s, 1, 7, p, sizeof p, p, sizeof p);
  s.step();
  s.step();

  bgm1::FrameHeader h;
  CHECK(bgm1::parse_frame_header(sock.written.data(), &h) == bgm1::Err::kOk);
  CHECK(h.flags == (bgm1::kFlagRoadIdr | bgm1::kFlagWideIdr));
  CHECK(h.frame_idx == 7);
}

// 在途槽 + 排队槽：排队只留最新，新包覆盖 = 丢旧包；门开着丢 = kDrop（断档）→
// 门重新关门（断档后首帧必须双路 IDR，18 号 #1：替代帧若是 P 帧不得开流）；
// 迟到旧包静默丢
static void test_sender_queue_overwrite() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  FrameScheduler sched;
  SchedStep connect_step;
  SchedStep drop_step;
  bool saw_drop = false;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) {
                   evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)});
                   if (e.ev == UplinkEvent::kNewConnection) {
                     connect_step = sched.on_connect();
                   } else if (e.ev == UplinkEvent::kDrop) {
                     saw_drop = true;
                     drop_step = sched.on_frame_dropped(e.frame_idx);
                   } else if (e.ev == UplinkEvent::kHeadBarrier) {
                     sched.on_frame_dropped(e.frame_idx);
                   }
                 });
  CHECK(s.step());
  CHECK(connect_step.request_keyframe_road && connect_step.request_keyframe_wide);
  evs.clear();

  sock.mode = FakeSock::kBlock;  // 写不动 → 头对帧 0 停在在途
  const uint8_t p[2] = {0xaa, 0xbb};
  SchedStep s0 = sched.on_frame_submit(FrameIdx{0});
  CHECK(s0.road_idr && s0.wide_idr);
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);  // 头对开流
  s.step();  // queued → inflight，写阻塞

  CHECK(!sched.on_frame_submit(FrameIdx{1}).any());
  s.submit_road(ConnEpoch{1}, FrameIdx{1}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{1}, FrameIdx{1}, p, sizeof p, false);
  s.step();  // 进排队槽
  CHECK(evs.empty());

  // 新包覆盖排队槽 = 丢弃帧 1（kDrop，码流断档）→ 序列头门关门
  CHECK(!sched.on_frame_submit(FrameIdx{2}).any());
  s.submit_road(ConnEpoch{1}, FrameIdx{2}, test_hdr(), p, sizeof p, false, false, false);
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kDrop && evs[0].idx == 1);
  CHECK(saw_drop && drop_step.drop_detected);
  CHECK(drop_step.request_keyframe_road && drop_step.request_keyframe_wide);

  // 替代帧 2 是 P 对 → 序列头门丢弃（kHeadBarrier），不得成为断档后首帧
  s.submit_wide(ConnEpoch{1}, FrameIdx{2}, p, sizeof p, false);
  s.step();
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kHeadBarrier && evs[1].idx == 2);

  // 事件落状态机：断档/屏障后下一个提交对 = 双路 IDR（预测）
  SchedStep after = sched.on_frame_submit(FrameIdx{3});
  CHECK(after.road_idr && after.wide_idr);

  // 帧 1 的迟到包（如 wide）静默丢弃，不重建已丢帧
  s.submit_wide(ConnEpoch{1}, FrameIdx{1}, p, sizeof p, false);
  CHECK(evs.size() == 2);

  // 双路 IDR 对（帧 3）重新开流 → 通了以后发的是帧 0、帧 3
  open_head(s, 1, 3, p, sizeof p, p, sizeof p);
  sock.mode = FakeSock::kWrite;
  s.step();  // 在途帧 0 写完（kFrameSent）
  s.step();  // 帧 3 进在途并写完
  bool sent0 = false, sent3 = false;
  for (auto& e : evs) {
    if (e.ev == UplinkEvent::kFrameSent && e.idx == 0) sent0 = true;
    if (e.ev == UplinkEvent::kFrameSent && e.idx == 3) sent3 = true;
  }
  CHECK(sent0 && sent3);
}

// 假死（≥200 ms 无进展）：上报 kStall，写得动就发完在途帧再重连
static void test_sender_stall_finish() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) {
                   evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)});
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
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);  // 头对开流
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

  CHECK(s.step());  // 下一次 step 重连（新连接代号 2，序列头门重新关门）
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kNewConnection && evs[2].ep == 2);
}

// 假死且写不动：截断（不发 kFrameSent），重连清空
static void test_sender_stall_truncate() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();
  evs.clear();

  sock.mode = FakeSock::kBlock;
  const uint8_t p[2] = {1, 2};
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);
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
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kNewConnection && evs[0].ep == 1);

  // 读侧发现断连 → 重连路径
  s.notify_disconnect();
  CHECK(!s.connected());

  sock.connect_ok = false;
  CHECK(!s.step());
  CHECK(!s.step());
  CHECK(evs.size() == 1);  // 前两次失败未升级
  CHECK(!s.step());  // 第 3 次失败 → 升级「连接丢失」
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kLinkLost);

  CHECK(!s.step());  // 之后继续重试，不重复升级
  CHECK(evs.size() == 2);

  sock.connect_ok = true;
  CHECK(s.step());
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kNewConnection && evs[2].ep == 2);
  CHECK(s.connected());
}

// 断连/未连接：submit_* 静默丢（不阻塞编码回调），队列不带进新连接
static void test_sender_submit_while_down() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  const uint8_t p[2] = {1, 2};

  s.submit_road(ConnEpoch{0}, FrameIdx{0}, test_hdr(), p, sizeof p, true, true, true);  // 未连接 → 丢
  s.submit_wide(ConnEpoch{0}, FrameIdx{0}, p, sizeof p, true);
  CHECK(evs.empty());
  CHECK(s.step());
  s.step();
  CHECK(evs.size() == 1);  // 只有 kNewConnection，无 kFrameSent

  // 建连后入队一帧（头对开流），再断连 → 旧帧不发
  sock.mode = FakeSock::kBlock;
  open_head(s, 1, 1, p, sizeof p, p, sizeof p);
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
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();

  const uint8_t road[10] = {0};
  const uint8_t wide[7] = {1};
  open_head(s, 1, 0, road, sizeof road, wide, sizeof wide);
  for (int i = 0; i < 100 && evs.size() < 2; i++) {
    now++;
    s.step();  // 假时钟推进避免中途判假死
  }
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 0);
  CHECK(sock.written.size() == bgm1::frame_wire_size(sizeof road, sizeof wide));
}

// 写错误 → 重连
// 发送线程空闲等待：提交即唤醒（不睡满轮询周期），无提交则超时
static void test_sender_wait_for_work_wakes_on_submit() {
  using namespace std::chrono;
  FakeSock sock;
  uint64_t now = 0;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{}, nullptr);
  s.step();
  CHECK(!s.wait_for_work(1));

  const uint8_t p[3] = {1, 2, 3};
  std::thread submitter([&] {
    std::this_thread::sleep_for(milliseconds(20));
    s.submit_road(s.conn_epoch(), FrameIdx{0}, test_hdr(), p, sizeof p, true, true, true);
  });
  const auto t0 = steady_clock::now();
  CHECK(s.wait_for_work(2000));
  CHECK(steady_clock::now() - t0 < milliseconds(1000));
  submitter.join();
  CHECK(!s.wait_for_work(0));  // 唤醒已消费

  s.submit_wide(s.conn_epoch(), FrameIdx{0}, p, sizeof p, true);  // 等待前已提交也不丢
  CHECK(s.wait_for_work(0));
}

// 90 号 C2：wait_for_work 的参数只是上限（退出检查兜底），实际等多久由发送状态定——
// 空闲睡满上限、只等事件；无事件可等的状态（未连接重试、socket 写阻塞）仍按 poll_ms 轮询；
// 在途帧只等 wide 时最晚在假死判定点醒
static void test_sender_wait_idle_sleeps_full_cap() {
  using namespace std::chrono;
  FakeSock sock;
  uint64_t now = 0;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{}, nullptr);
  s.step();
  const auto t0 = steady_clock::now();
  CHECK(!s.wait_for_work(60));
  CHECK(steady_clock::now() - t0 >= milliseconds(50));
}

static void test_sender_wait_disconnected_keeps_retry_gap() {
  using namespace std::chrono;
  FakeSock sock;
  sock.connect_ok = false;
  uint64_t now = 0;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{}, nullptr);
  CHECK(!s.step());  // 建连失败
  const auto t0 = steady_clock::now();
  CHECK(!s.wait_for_work(1000));
  CHECK(steady_clock::now() - t0 < milliseconds(200));  // 重连间隔仍是 poll_ms，不睡满上限
}

static void test_sender_wait_socket_blocked_keeps_polling() {
  using namespace std::chrono;
  FakeSock sock;
  uint64_t now = 0;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{}, nullptr);
  s.step();
  sock.mode = FakeSock::kBlock;
  const uint8_t p[3] = {1, 2, 3};
  open_head(s, u32(s.conn_epoch()), 0, p, sizeof p, p, sizeof p);
  s.step();            // 转在途，写不动
  s.wait_for_work(0);  // 消费提交唤醒
  const auto t0 = steady_clock::now();
  CHECK(!s.wait_for_work(1000));
  CHECK(steady_clock::now() - t0 < milliseconds(200));  // socket 可写无事件：按 poll_ms 重试
}

static void test_sender_wait_wide_until_stall_deadline() {
  using namespace std::chrono;
  FakeSock sock;
  uint64_t now = 0;
  UplinkSenderConfig cfg;
  cfg.stall_ms = 60;
  UplinkSender s(&sock, [&] { return now; }, cfg, nullptr);
  s.step();
  const uint8_t p[3] = {1, 2, 3};
  open_head(s, u32(s.conn_epoch()), 0, p, sizeof p, p, sizeof p);
  s.step();  // 头对发完，开流
  s.submit_road(s.conn_epoch(), FrameIdx{1}, test_hdr(), p, sizeof p, false, false, false);
  s.step();  // chunk1 发出，在途帧等 wide
  s.wait_for_work(0);
  now += 20;  // 距在途帧起点已过 20 ms：离假死判定还剩 40 ms
  const auto t0 = steady_clock::now();
  CHECK(!s.wait_for_work(1000));
  const auto dt = steady_clock::now() - t0;
  CHECK(dt >= milliseconds(30) && dt < milliseconds(500));
}

static void test_sender_notify_disconnect_wakes_writer() {
  using namespace std::chrono;
  FakeSock sock;
  uint64_t now = 0;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{}, nullptr);
  s.step();
  std::thread reader([&] {
    std::this_thread::sleep_for(milliseconds(20));
    s.notify_disconnect();
  });
  const auto t0 = steady_clock::now();
  CHECK(s.wait_for_work(2000));  // 读侧断连：发送线程立即醒来走重连
  CHECK(steady_clock::now() - t0 < milliseconds(1000));
  reader.join();
  CHECK(!s.connected());
}

static void test_sender_write_error() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();

  sock.mode = FakeSock::kErr;
  const uint8_t p[2] = {1, 2};
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);
  s.step();
  CHECK(!s.connected());
  sock.mode = FakeSock::kWrite;
  CHECK(s.step());
  CHECK(s.connected());
}

// =====================================================================
// LinkStateTracker（06 号）：HELLO instance_id 对比区分服务端重启/网络闪断，
// 对外（Param "BigmodelLinkState"）报状态串：connecting/connected/blip/restart/lost
// =====================================================================

static void test_link_state() {
  LinkStateTracker t;
  CHECK(t.value() == "connecting");  // 起步 = 连接中

  t.on_hello(0xA);
  CHECK(t.value() == "connected");   // 首次见到 instance = 已连接

  t.on_connecting();
  CHECK(t.value() == "connecting");  // 断连重连中
  t.on_hello(0xA);
  CHECK(t.value() == "blip");        // 同 instance 重连 = 网络闪断

  t.on_connecting();
  t.on_hello(0xB);
  CHECK(t.value() == "restart");     // instance 变了 = 服务端重启

  t.on_connecting();
  t.on_hello(0xB);
  CHECK(t.value() == "blip");        // 重启后再断再连同 instance = 闪断

  t.on_lost();
  CHECK(t.value() == "lost");        // 连败升级「连接丢失」
  t.on_hello(0xB);
  CHECK(t.value() == "blip");        // lost 后连回同 instance 仍算闪断

  t.on_connecting();
  t.on_hello(0xC);
  CHECK(t.value() == "restart");     // lost 后换 instance = 重启

  t.on_version_mismatch();
  CHECK(t.value() == "version_mismatch");  // HELLO kBadVersion（锁步升级，不做兼容）
  t.on_connecting();
  CHECK(t.value() == "version_mismatch");  // M2：重连中不能被 connecting 覆盖
  t.on_version_mismatch();
  t.on_lost();
  CHECK(t.value() == "version_mismatch");  // kLinkLost 也不能覆盖
  t.on_hello(0xC);
  CHECK(t.value() == "blip");              // 下一次 on_hello 成功才解除
}

// =====================================================================
// 06 号：mDNS 发现（avahi-browse 行解析 + 发现节流策略）
// =====================================================================

static void test_avahi_parse() {
  ServerEndpoint ep;
  // avahi-browse -rpt 可解析行：=;iface;IPv4;name;type;domain;host;ip;port;txt...
  CHECK(parse_avahi_browse_line(
      "=;wlan0;IPv4;8e5-uuid;_bigmodel._tcp;local;8e5.local;172.20.10.2;7070;\"ver\"=\"1\";\"name\"=\"8E5\"",
      &ep));
  CHECK(ep.host == "172.20.10.2" && ep.port == 7070 && ep.name == "8e5-uuid" && ep.iface == "wlan0");
  CHECK(!parse_avahi_browse_line("+;wlan0;IPv4;8e5;_bigmodel._tcp;local;8e5.local;172.20.10.2;7070", &ep));
  CHECK(!parse_avahi_browse_line("=;wlan0;IPv6;8e5;_bigmodel._tcp;local;8e5.local;fe80::1;7070", &ep));
  CHECK(!parse_avahi_browse_line("=;wlan0;IPv4;x;_http._tcp;local;x.local;1.2.3.4;80", &ep));
  CHECK(!parse_avahi_browse_line("garbage", &ep));
  CHECK(!parse_avahi_browse_line("=;wlan0;IPv4;8e5;_bigmodel._tcp;local;8e5.local;172.20.10.2", &ep));
}

static void test_avahi_scope() {
  // 06 号：只在 C4 当前连接的 Wi-Fi 子网内发现——接口对、IP 落在 wlan0 子网内才收
  SubnetScope scope;
  scope.iface = "wlan0";
  CHECK(parse_ipv4("172.20.10.2", &scope.local));
  CHECK(parse_ipv4("255.255.255.0", &scope.mask));

  ServerEndpoint e;
  e.host = "172.20.10.9"; e.iface = "wlan0";
  CHECK(in_scope(e, scope));            // 同接口同子网（手机热点）
  e.host = "172.20.10.254";
  CHECK(in_scope(e, scope));
  e.host = "192.168.1.7";
  CHECK(!in_scope(e, scope));           // 别的子网（家里路由/陈旧缓存）不收
  e.host = "172.20.11.9";
  CHECK(!in_scope(e, scope));           // 相邻子网不收
  e.host = "172.20.10.9"; e.iface = "eth0";
  CHECK(!in_scope(e, scope));           // 别的接口不收

  CHECK(!parse_ipv4("garbage", &scope.local));
  CHECK(!parse_ipv4("1.2.3", &scope.local));
}

static void test_avahi_locator() {
  int browses = 0;
  ServerEndpoint found{"10.0.0.5", 7070, "a", ""};
  AvahiLocator loc([&](ServerEndpoint* out) { browses++; *out = found; return true; }, 3);
  ServerEndpoint ep;
  CHECK(loc.resolve(&ep) && ep.host == "10.0.0.5" && browses == 1);  // 首次现查
  CHECK(loc.resolve(&ep) && browses == 1);                           // 缓存直连（闪断快速重连）
  loc.on_connect_failure();
  loc.on_connect_failure();
  CHECK(loc.resolve(&ep) && browses == 1);                           // 连败 < 3 仍用缓存
  loc.on_connect_failure();                                          // 第 3 次 = kLinkLost 升级点
  CHECK(loc.resolve(&ep) && browses == 2);                           // 回到 mDNS 发现
  found = {"10.0.0.9", 7070, "a", ""};
  loc.on_connect_failure();
  loc.on_connect_failure();
  loc.on_connect_failure();
  CHECK(loc.resolve(&ep) && ep.host == "10.0.0.9" && browses == 3);  // 手机换 IP 跟上

  AvahiLocator none([&](ServerEndpoint*) { return false; }, 3);      // 热点没开/App 没跑
  CHECK(!none.resolve(&ep));
  CHECK(!none.resolve(&ep));                                         // 没缓存，持续重试

  FixedLocator fix({"192.168.1.7", 7070, "", ""});                   // 手动 IP 兜底
  CHECK(fix.resolve(&ep) && ep.host == "192.168.1.7" && ep.port == 7070);
  fix.on_connect_failure();
  CHECK(fix.resolve(&ep) && ep.host == "192.168.1.7");               // 手动恒给定值，不发现
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
  // v3 常量与缺省：头 168 B，缺省 GNSS 全 0（无定位）、VEHICLE 为无效值
  CHECK(bgm1::kVersion == 3 && bgm1::kFrameHdrSize == 168 && bgm1::frame_wire_size(1000, 700) == 1888);
  CHECK(buf[4] == 3 && !fv.hdr.gnss.has_fix() && fv.hdr.vehicle.yaw_rate_centideg_per_s == INT16_MIN &&
        fv.hdr.vehicle.bearing_accuracy_half_deg == 255);
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
  // 旧 v2（160 B 头）/ 未来 v4：ver 在前 16 B 先拒
  bad = buf;
  bad[4] = 2;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kBadVersion);
  bad[4] = 4;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kBadVersion);
  CHECK(bgm1::version_mismatch_detail(2, 3) == 0x0203u);
  // road_len 超上限 → kLenTooLarge（不是截断）
  bad = buf;
  bad[12] = 0xff; bad[13] = 0xff; bad[14] = 0xff; bad[15] = 0xff;
  CHECK(bgm1::parse_frame(bad.data(), bad.size(), &fv) == bgm1::Err::kLenTooLarge);

  // GNSS / VEHICLE 往返（边界 + 饱和）
  bgm1::FrameHeader g = test_hdr();
  g.road_len = 11;
  g.wide_len = 5;
  g.gnss = {900000000, -1800000000, 0xFFFE, 35999, bgm1::kGnssHorizontalAccuracyMaxDm,
            bgm1::make_fix_age_and_valid_flags(100000, true, true, true)};
  g.vehicle = {32767, bgm1::kVehicleYawValid | bgm1::kVehicleReverse, bgm1::kGnssBearingAccuracyMaxHalfDeg};
  CHECK(bgm1::pack_frame(g, road, wide, buf.data(), buf.size()) == buf.size());
  CHECK(bgm1::parse_frame(buf.data(), buf.size(), &fv) == bgm1::Err::kOk);
  CHECK(fv.hdr.gnss.latitude_deg_x1e7 == 900000000 && fv.hdr.gnss.longitude_deg_x1e7 == -1800000000 && fv.hdr.gnss.bearing_centideg == 35999);
  CHECK(fv.hdr.gnss.age_ms() == 4095 && fv.hdr.gnss.has_fix() && fv.hdr.gnss.horizontal_accuracy_dm == 0xFFFE);
  CHECK(fv.hdr.vehicle.yaw_rate_centideg_per_s == 32767 && fv.hdr.vehicle.reversing() && fv.hdr.vehicle.yaw_valid() &&
        !fv.hdr.vehicle.standstill() && fv.hdr.vehicle.bearing_accuracy_half_deg == 254);
  CHECK(buf[164] == 0 && buf[165] == 0 && buf[166] == 0 && buf[167] == 0);  // VEHICLE 保留 4 B 恒 0

  // HELLO 往返（06 号；同源副本守卫：与 android/ 的 frame_codec 同一实现）
  bgm1::Hello hello;
  hello.instance_id = 0x0123456789ABCDEFULL;
  hello.max_frame = 1u << 20;
  std::vector<uint8_t> hbuf(bgm1::kHelloWireSize);
  CHECK(bgm1::pack_hello(hello, hbuf.data(), hbuf.size()) == bgm1::kHelloWireSize);
  bgm1::Hello h2;
  CHECK(bgm1::parse_hello(hbuf.data(), hbuf.size(), &h2) == bgm1::Err::kOk);
  CHECK(h2.instance_id == hello.instance_id && h2.max_frame == hello.max_frame);
  CHECK(bgm1::parse_hello(hbuf.data(), bgm1::kHelloWireSize - 1, &h2) == bgm1::Err::kTruncated);
  // golden 逐字节钉死（app/tests/fixtures/hello_golden.bin 同一样例，跨仓库镜像互证；ver=3）
  static const uint8_t kHelloGolden[bgm1::kHelloWireSize] = {
      0x42, 0x47, 0x4D, 0x31, 0x03, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
      0x10, 0x00, 0x00, 0x00, 0xEF, 0xCD, 0xAB, 0x89, 0x67, 0x45, 0x23, 0x01,
      0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
  };
  CHECK(std::memcmp(hbuf.data(), kHelloGolden, bgm1::kHelloWireSize) == 0);
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
  // v3：REPLY 8480 B / len 8440；缺省 Reply 的 NavExt 160 B 全 0（present_flags=0 = 无导航数据）
  CHECK(bgm1::kReplyWireSize == 8480 && bgm1::kReplyPayloadSize == 8440 && bgm1::kReplyNavExtSize == 160);
  bool nav_zero = out.nav_ext.present_flags == 0 && out.nav_ext.nav_health_flags == 0;
  for (size_t i = 8304; i < 8464; i++) nav_zero &= buf[i] == 0;
  CHECK(nav_zero);

  // NavExt 往返：四个子结构 + present_flags / nav_health_flags 位；CURVE point_count=3 → 第 3–7 点 pack 为 0
  r.nav_ext.present_flags = bgm1::kNavPresentNavState | bgm1::kNavPresentRoadAttr | bgm1::kNavPresentCurveAhead |
                      bgm1::kNavPresentNavDebug;
  r.nav_ext.nav_health_flags = bgm1::kNavHealthGpsOk | bgm1::kNavHealthRerouting;
  r.nav_ext.nav_state = {3, 20, 255, 2, 350, 0xFFFFFFFFu, 987, 40, 0x0F, 254, 0xFFFF};
  r.nav_ext.road_attr = {80, 1, 0, 255, 0xFFFF, 1000, 0x85, 3, 25};
  r.nav_ext.curve_ahead.point_count = 3;
  r.nav_ext.curve_ahead.max_lateral_acceleration_dm_per_s2 = 20;
  r.nav_ext.curve_ahead.horizon_distance_m = 600;
  for (int i = 0; i < 8; i++) r.nav_ext.curve_ahead.points[i] = {1000 * i, -2000 * i, uint16_t(50 + i), uint8_t(40 + i), uint8_t(i)};
  r.nav_ext.nav_debug = {4200, 1800, 35, 27};
  CHECK(bgm1::pack_reply(r, buf.data(), buf.size()) == bgm1::kReplyWireSize);
  bool tail_zero = true;
  for (size_t i = 8356 + 12 * 3; i < 8452; i++) tail_zero &= buf[i] == 0;
  CHECK(tail_zero);
  CHECK(bgm1::parse_reply(buf.data(), buf.size(), &out) == bgm1::Err::kOk);
  CHECK(out.nav_ext.present_flags == 0x0F && out.nav_ext.nav_health_flags == 0x09);
  CHECK(out.nav_ext.nav_state.maneuver_type == 20 && out.nav_ext.nav_state.next_maneuver_type == 255 &&
        out.nav_ext.nav_state.route_remaining_m == 0xFFFFFFFFu && out.nav_ext.nav_state.heading_centideg == 0xFFFF);
  CHECK(out.nav_ext.road_attr.speed_limit_kph == 80 && out.nav_ext.road_attr.road_class == 255 &&
        out.nav_ext.road_attr.speed_limit_ahead_distance_m == 0xFFFF && out.nav_ext.road_attr.road_edge_flags == 0x85);
  CHECK(out.nav_ext.curve_ahead.point_count == 3 && out.nav_ext.curve_ahead.points[2].latitude_deg_x1e7 == 2000 &&
        out.nav_ext.curve_ahead.points[3].latitude_deg_x1e7 == 0 && out.nav_ext.curve_ahead.points[7].distance_along_route_m == 0);
  CHECK(out.nav_ext.nav_debug.render_duration_us == 4200 && out.nav_ext.nav_debug.nav_cpu_usage_percent == 27);
  // present_flags 位为 0 的子结构读作 0（即使线上有垃圾）
  std::vector<uint8_t> garbage = buf;
  garbage[8304] = bgm1::kNavPresentNavState;
  std::memset(garbage.data() + 8332, 0x55, 120);
  CHECK(bgm1::parse_reply(garbage.data(), garbage.size(), &out) == bgm1::Err::kOk);
  CHECK(out.nav_ext.nav_state.maneuver_type == 20 && out.nav_ext.road_attr.speed_limit_kph == 0 &&
        out.nav_ext.curve_ahead.point_count == 0 && out.nav_ext.nav_debug.render_duration_us == 0);

  CHECK(bgm1::parse_reply(buf.data(), buf.size() - 1, &out) == bgm1::Err::kTruncated);
  // len 不符 / 多 1 字节 → kLenMismatch；旧 v2 的 8320 B REPLY（len=8280）→ kBadVersion（ver 先于长度）
  std::vector<uint8_t> lenbad = buf;
  lenbad[12] = 0x58; lenbad[13] = 0x20;  // len = 8280
  CHECK(bgm1::parse_reply(lenbad.data(), lenbad.size(), &out) == bgm1::Err::kLenMismatch);
  lenbad = buf;
  lenbad.push_back(0);
  CHECK(bgm1::parse_reply(lenbad.data(), lenbad.size(), &out) == bgm1::Err::kLenMismatch);
  std::vector<uint8_t> v2(buf.begin(), buf.begin() + 8320);
  v2[4] = 2;
  v2[12] = 0x58; v2[13] = 0x20;
  CHECK(bgm1::parse_reply(v2.data(), v2.size(), &out) == bgm1::Err::kBadVersion);
  std::vector<uint8_t> bad = buf;
  bad[5] = bgm1::kTypeFrame;
  CHECK(bgm1::parse_reply(bad.data(), bad.size(), &out) == bgm1::Err::kBadType);

  bgm1::ErrMsg e;
  e.code = bgm1::kErrVersionMismatch;
  e.detail = bgm1::version_mismatch_detail(2, bgm1::kVersion);
  e.frame_idx = 13;
  std::vector<uint8_t> ebuf(bgm1::kErrWireSize);
  CHECK(bgm1::pack_err(e, ebuf.data(), ebuf.size()) == bgm1::kErrWireSize);
  bgm1::ErrMsg eout;
  CHECK(bgm1::parse_err(ebuf.data(), ebuf.size(), &eout) == bgm1::Err::kOk);
  CHECK(eout.code == bgm1::kErrVersionMismatch && eout.detail == 0x0203u && eout.frame_idx == 13);
  CHECK(bgm1::parse_err(ebuf.data(), ebuf.size() - 1, &eout) == bgm1::Err::kTruncated);
}


// v3 规范化（与手机 test_frame_codec.cpp 同口径）：has_fix=0、CURVE 坏块、保留位掩码、yaw 一致性
static void test_v3_normalisation() {
  uint8_t buf[bgm1::kFrameHdrSize];
  bgm1::FrameHeader back;
  auto le16 = [&](const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); };

  // has_fix=0：位置类字段与 bearing_accuracy_half_deg 规范化；speed + speed_valid + age 保留
  bgm1::FrameHeader nofix;
  nofix.gnss = {123, -456, 789, 1011, 12, bgm1::make_fix_age_and_valid_flags(50, false, true, true)};
  nofix.vehicle = {-300, bgm1::kVehicleYawValid, 7};
  bgm1::pack_frame_header(nofix, buf);
  bool pos_zero = true;
  for (int i = 144; i < 152; i++) pos_zero &= buf[i] == 0;
  CHECK(pos_zero && le16(buf + 154) == 0 && le16(buf + 156) == 0);
  CHECK(le16(buf + 152) == 789 && le16(buf + 158) == (50 | bgm1::kGnssSpeedValid) && buf[163] == 255);
  CHECK(bgm1::parse_frame_header(buf, &back) == bgm1::Err::kOk);
  CHECK(!back.gnss.has_fix() && back.gnss.speed_cm_per_s == 789 && back.gnss.speed_valid() && back.gnss.age_ms() == 50 &&
        !back.gnss.bearing_valid() && back.gnss.latitude_deg_x1e7 == 0 && back.vehicle.bearing_accuracy_half_deg == 255 &&
        back.vehicle.yaw_rate_centideg_per_s == -300);
  // has_fix=1：带 valid 位的字段以 valid 位为准（无效写 0），horizontal_accuracy（无 valid 位）保留 0xFFFF 哨兵
  bgm1::FrameHeader gv;
  gv.gnss = {374220000, -1220840000, 500, 9000, 0xFFFF, bgm1::make_fix_age_and_valid_flags(10, true, false, true)};
  bgm1::pack_frame_header(gv, buf);
  CHECK(le16(buf + 152) == 0 && le16(buf + 154) == 9000 && le16(buf + 156) == 0xFFFF);  // speed_valid=0
  CHECK(bgm1::parse_frame_header(buf, &back) == bgm1::Err::kOk && back.gnss.speed_cm_per_s == 0 && !back.gnss.speed_valid() &&
        back.gnss.bearing_centideg == 9000 && back.gnss.horizontal_accuracy_dm == 0xFFFF);
  gv.gnss.fix_age_and_valid_flags = bgm1::make_fix_age_and_valid_flags(10, true, true, false);
  bgm1::pack_frame_header(gv, buf);
  CHECK(le16(buf + 152) == 500 && le16(buf + 154) == 0);  // bearing_valid=0
  CHECK(bgm1::parse_frame_header(buf, &back) == bgm1::Err::kOk && back.gnss.bearing_centideg == 0 && !back.gnss.bearing_valid() &&
        back.gnss.speed_cm_per_s == 500);
  buf[154] = 0x78; buf[155] = 0x56;  // 线上 bearing_valid=0 时的垃圾
  CHECK(bgm1::parse_frame_header(buf, &back) == bgm1::Err::kOk && back.gnss.bearing_centideg == 0);
  bgm1::FrameHeader empty;  // 完全无数据：GNSS 整块 0
  bgm1::pack_frame_header(empty, buf);
  bool zero16 = true;
  for (int i = 144; i < 160; i++) zero16 &= buf[i] == 0;
  CHECK(zero16);

  // 保留位强制 0；yaw 与 yaw_valid 一致
  bgm1::FrameHeader rsv;
  rsv.gnss = {1, 2, 3, 4, 5, 0xFFFF};
  rsv.vehicle = {100, 0xFF, 7};
  bgm1::pack_frame_header(rsv, buf);
  CHECK(le16(buf + 158) == 0xDFFF && buf[162] == 0x07);
  rsv.vehicle = {bgm1::kYawRateInvalidCentidegPerS, bgm1::kVehicleYawValid | bgm1::kVehicleStandstill, 7};
  bgm1::pack_frame_header(rsv, buf);
  CHECK(buf[162] == bgm1::kVehicleStandstill);
  rsv.vehicle = {-1234, bgm1::kVehicleReverse, 7};
  bgm1::pack_frame_header(rsv, buf);
  CHECK(buf[160] == 0x00 && buf[161] == 0x80 && buf[162] == bgm1::kVehicleReverse);

  // NavExt 保留位
  std::vector<uint8_t> rb(bgm1::kReplyWireSize);
  bgm1::Reply r;
  r.nav_ext.present_flags = 0xFF;
  r.nav_ext.nav_health_flags = 0xFF;
  r.nav_ext.nav_state.nav_input_flags = 0xFF;
  r.nav_ext.road_attr.road_edge_flags = 0xFF;
  r.nav_ext.curve_ahead.curve_flags = 0xFF;
  bgm1::pack_reply(r, rb.data(), rb.size());
  CHECK(rb[8304] == 0x0F && rb[8305] == 0x0F && rb[8308 + 18] == 0x0F && rb[8332 + 8] == 0xBF && rb[8348 + 6] == 0x07);

  // CURVE point_count>8：打包钳到 8；线上 point_count>8 = 坏块，清 present_flags bit2、子块整块缺省
  r = bgm1::Reply{};
  r.nav_ext.present_flags = bgm1::kNavPresentCurveAhead | bgm1::kNavPresentNavDebug;
  r.nav_ext.curve_ahead.point_count = 200;
  r.nav_ext.curve_ahead.horizon_distance_m = 600;
  r.nav_ext.nav_debug.render_duration_us = 4200;
  bgm1::pack_reply(r, rb.data(), rb.size());
  CHECK(rb[8348] == 8);
  rb[8348] = 9;
  bgm1::Reply out;
  CHECK(bgm1::parse_reply(rb.data(), rb.size(), &out) == bgm1::Err::kOk);
  CHECK(!(out.nav_ext.present_flags & bgm1::kNavPresentCurveAhead) && (out.nav_ext.present_flags & bgm1::kNavPresentNavDebug));
  CHECK(out.nav_ext.curve_ahead.point_count == 0 && out.nav_ext.curve_ahead.horizon_distance_m == 0 &&
        out.nav_ext.curve_ahead.points[0].latitude_deg_x1e7 == 0);
  CHECK(out.nav_ext.nav_debug.render_duration_us == 4200);  // 其余子块不受影响
}

// =====================================================================
// 跨端 golden 互解：读 chipmunk 仓库 app/tests/fixtures（Python 生成）的四份字节样例，
//   车机 parse 出字段与期望一致，车机 pack 逐字节等于 golden（=手机 C++/Python 解析的同一份字节）。
// golden 被 .gitignore 的 *.bin 排除、不在本仓库，故由 BGM1_GOLDEN_DIR 指定目录；默认必跑，
// 未设置或文件缺失 / 为空都判失败，显式跳过用 BGM1_GOLDEN_SKIP=1。
// 期望值与 chipmunk app/tests/make_frame_golden.py 一致（改动须同步）。
// =====================================================================

static bool read_golden(const std::string& dir, const char* name, std::vector<uint8_t>* out) {
  FILE* f = fopen((dir + "/" + name).c_str(), "rb");
  if (!f) return false;
  uint8_t b[4096];
  size_t n;
  out->clear();
  while ((n = fread(b, 1, sizeof b, f)) > 0) out->insert(out->end(), b, b + n);
  fclose(f);
  return true;
}

static void test_cross_end_golden() {
  const char* dir = std::getenv("BGM1_GOLDEN_DIR");
  const char* skip = std::getenv("BGM1_GOLDEN_SKIP");
  if (skip && skip[0] == '1') {
    printf("SKIP test_cross_end_golden（BGM1_GOLDEN_SKIP=1，显式跳过）\n");
    return;
  }
  if (!dir) {  // 默认必跑：没指目录就是配置错误，不是静默跳过
    printf("FAIL test_cross_end_golden：未设置 BGM1_GOLDEN_DIR（显式跳过用 BGM1_GOLDEN_SKIP=1）\n");
    CHECK(dir != nullptr);
    return;
  }
  std::vector<uint8_t> gf, gr, ge, gh;
  CHECK(read_golden(dir, "frame_hevc_golden.bin", &gf) && !gf.empty());
  CHECK(read_golden(dir, "reply_golden.bin", &gr) && !gr.empty());
  CHECK(read_golden(dir, "err_golden.bin", &ge) && !ge.empty());
  CHECK(read_golden(dir, "hello_golden.bin", &gh) && !gh.empty());
  if (gf.empty() || gr.empty() || ge.empty() || gh.empty()) return;

  // FRAME：parse 字段 + pack == golden
  bgm1::FrameHeader h;
  h.flags = bgm1::kFlagRoadIdr | bgm1::kFlagWideIdr;
  h.frame_idx = 0x11223344u;
  h.road_len = 1000;
  h.wide_len = 700;
  h.t_eof = 0x0011223344556677ull;
  const float desire[8] = {0.5f, 1.5f, -2.25f, 3.75f, -0.125f, 0.0f, 2.0f, -1.0f};
  std::memcpy(h.desire, desire, sizeof desire);
  h.traffic_convention[0] = 1.0f;
  h.action_t[0] = 1.25f;
  h.action_t[1] = -0.5f;
  for (int i = 0; i < 9; i++) {
    h.warp_road[i] = i * 0.25f + 0.5f;
    h.warp_wide[i] = 1.25f - i * 0.5f;
  }
  h.gnss = {374220000, -1220840000, 1388, 27315, 35, 0xD050};
  h.vehicle = {-1234, bgm1::kVehicleYawValid | bgm1::kVehicleStandstill, 6};
  std::vector<uint8_t> road(1000), wide(700), packed(gf.size());
  for (size_t i = 0; i < road.size(); i++) road[i] = (uint8_t)(i % 251);
  for (size_t i = 0; i < wide.size(); i++) wide[i] = (uint8_t)((i * 7 + 3) % 256);
  CHECK(bgm1::pack_frame(h, road.data(), wide.data(), packed.data(), packed.size()) == gf.size());
  CHECK(packed == gf);
  bgm1::FrameView fv;
  CHECK(bgm1::parse_frame(gf.data(), gf.size(), &fv) == bgm1::Err::kOk);
  CHECK(fv.hdr.gnss.latitude_deg_x1e7 == 374220000 && fv.hdr.gnss.fix_age_and_valid_flags == 0xD050 && fv.hdr.gnss.age_ms() == 80);
  CHECK(fv.hdr.vehicle.yaw_rate_centideg_per_s == -1234 && fv.hdr.vehicle.bearing_accuracy_half_deg == 6 && fv.hdr.vehicle.standstill());

  // REPLY：parse 后重新 pack 逐字节回 golden，并核对 NavExt 关键字段
  bgm1::Reply r;
  CHECK(bgm1::parse_reply(gr.data(), gr.size(), &r) == bgm1::Err::kOk);
  CHECK(r.nav_ext.present_flags == 0x0F && r.nav_ext.nav_health_flags == 0x07);
  CHECK(r.nav_ext.nav_state.maneuver_type == 26 && r.nav_ext.nav_state.route_remaining_m == 12345);
  CHECK(r.nav_ext.road_attr.speed_limit_kph == 80 && r.nav_ext.road_attr.road_edge_flags == 0x85);
  CHECK(r.nav_ext.curve_ahead.point_count == 8 && r.nav_ext.curve_ahead.points[7].latitude_deg_x1e7 == 374220000 + 7000 &&
        r.nav_ext.curve_ahead.points[7].curvature_per_m_x1e4 == 150);
  CHECK(r.nav_ext.nav_debug.render_duration_us == 4200 && r.nav_ext.nav_debug.nav_cpu_usage_percent == 27);
  std::vector<uint8_t> rpacked(bgm1::kReplyWireSize);
  CHECK(bgm1::pack_reply(r, rpacked.data(), rpacked.size()) == bgm1::kReplyWireSize);
  CHECK(rpacked == gr);

  // ERR / HELLO
  bgm1::ErrMsg e;
  CHECK(bgm1::parse_err(ge.data(), ge.size(), &e) == bgm1::Err::kOk);
  CHECK(e.code == bgm1::kErrBadFrame && e.detail == 7 && e.frame_idx == 0x11223344u);
  std::vector<uint8_t> epacked(bgm1::kErrWireSize);
  CHECK(bgm1::pack_err(e, epacked.data(), epacked.size()) == bgm1::kErrWireSize && epacked == ge);
  bgm1::Hello hello;
  CHECK(bgm1::parse_hello(gh.data(), gh.size(), &hello) == bgm1::Err::kOk);
  std::vector<uint8_t> hpacked(bgm1::kHelloWireSize);
  CHECK(bgm1::pack_hello(hello, hpacked.data(), hpacked.size()) == bgm1::kHelloWireSize && hpacked == gh);
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
  // v3：没有 GNSS / deviceMotion 样本 → 「无数据」（GNSS 全 0 = 无定位；yaw 无效、航向精度未知）
  CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.longitude_deg_x1e7 == 0 && h.gnss.speed_cm_per_s == 0 && h.gnss.bearing_centideg == 0 &&
        h.gnss.horizontal_accuracy_dm == 0 && h.gnss.fix_age_and_valid_flags == 0 && !h.gnss.has_fix());
  CHECK(h.vehicle.yaw_rate_centideg_per_s == bgm1::kYawRateInvalidCentidegPerS && h.vehicle.vehicle_state_flags == 0 &&
        h.vehicle.bearing_accuracy_half_deg == bgm1::kGnssBearingAccuracyUnknownHalfDeg);
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

  // capture 线程 warp 用的矩阵 == 帧头矩阵（协议 v2：帧头记录 C4 已应用的矩阵）
  float road[9], wide[9];
  mp.warp(false, road);
  mp.warp(true, wide);
  CHECK(std::memcmp(road, h.warp_road, sizeof road) == 0);
  CHECK(std::memcmp(wide, h.warp_wide, sizeof wide) == 0);
}

// 04 号 C-2：modeld 上行元数据（modelDataV2SP）经 MetaProvider 进帧头——
// action_t 直通 + desire 电平→pulse 边沿（MODEL_ABI §4.2）
static void test_meta_provider_model_inputs() {
  MetaProvider mp;
  bgm1::FrameHeader h;
  std::memset(&h, 0x5a, sizeof h);

  // action_t 从 modeld 直通（chestnut 公式在 modeld 侧算，本模块不推导）
  const float at[2] = {0.125f, 0.45f};
  mp.set_model_inputs(at, 3);
  mp.fill(1, &h);
  CHECK(h.action_t[0] == 0.125f && h.action_t[1] == 0.45f);

  // 更新跟随
  const float at2[2] = {0.2f, 0.5f};
  mp.set_model_inputs(at2, 0);
  mp.fill(2, &h);
  CHECK(h.action_t[0] == 0.2f && h.action_t[1] == 0.5f);

  // desire 电平 → pulse 边沿（MODEL_ABI §4.2：通道 0 恒 0、持续同 desire 只 pulse 一次）
  MetaProvider mp2;
  mp2.set_model_inputs(at, 3);
  mp2.fill(10, &h);
  CHECK(h.desire[3] == 1.f);
  for (int i = 0; i < 8; i++) if (i != 3) CHECK(h.desire[i] == 0.f);

  mp2.fill(11, &h);                       // 同 desire 持续 → 不再 pulse
  for (int i = 0; i < 8; i++) CHECK(h.desire[i] == 0.f);

  mp2.set_model_inputs(at, 0);            // 回 0 → 无 pulse
  mp2.fill(12, &h);
  for (int i = 0; i < 8; i++) CHECK(h.desire[i] == 0.f);

  mp2.set_model_inputs(at, 3);            // 0→3 再现 → 再 pulse
  mp2.fill(13, &h);
  CHECK(h.desire[3] == 1.f);

  mp2.set_model_inputs(at, 5);            // 3→5 直接切换 → pulse 到新通道
  mp2.fill(14, &h);
  CHECK(h.desire[5] == 1.f && h.desire[3] == 0.f);

  mp2.set_model_inputs(at, 0);
  mp2.fill(15, &h);
  for (int i = 0; i < 8; i++) CHECK(h.desire[i] == 0.f);  // 通道 0 恒 0

  mp2.set_model_inputs(at, 9);            // 越界电平（msgq 垃圾）→ 不写任何通道
  mp2.fill(16, &h);
  for (int i = 0; i < 8; i++) CHECK(h.desire[i] == 0.f);

  // 新序列清 previousDesire（MODEL_ABI §4.3）：持续中的 desire 在新序列首帧重出 pulse
  MetaProvider mp3;
  mp3.set_model_inputs(at, 3);
  mp3.fill(20, &h);
  CHECK(h.desire[3] == 1.f);
  mp3.fill(21, &h);
  for (int i = 0; i < 8; i++) CHECK(h.desire[i] == 0.f);  // 持续同 desire 不再 pulse
  mp3.reset_desire_latch();                               // 双路 IDR/重连 → 新序列
  mp3.fill(22, &h);
  CHECK(h.desire[3] == 1.f);                              // 重出 pulse（d→0→d）
}

// ---- v3 FRAME GNSS / VEHICLE（票 B，第二轮：deviceMotion 源）----
// fill 只填原始编码；has_fix=0 清位置类字段、valid=0 写 0、无 fix 时 bearing_accuracy_half_deg=255
// 都是票 A 的 pack 规范形。这里 fill → pack → parse 验最终线上语义。

static bgm1::FrameHeader fill_wire(MetaProvider& mp, uint64_t t_eof) {
  bgm1::FrameHeader h;
  std::memset(&h, 0x5a, sizeof h);
  mp.fill(t_eof, &h);
  uint8_t wire[bgm1::kFrameHdrSize];
  bgm1::pack_frame_header(h, wire);
  bgm1::FrameHeader back;
  CHECK(bgm1::parse_frame_header(wire, &back) == bgm1::Err::kOk);
  return back;
}

static GnssSample make_gnss(double lat, double lon, double bearing_deg, double horizontal_accuracy_m,
                            double bearing_accuracy_deg, bool has_fix, bool event_valid, uint64_t message_time_ns) {
  GnssSample g;
  g.latitude_deg = lat;
  g.longitude_deg = lon;
  g.bearing_deg = bearing_deg;
  g.horizontal_accuracy_m = horizontal_accuracy_m;
  g.bearing_accuracy_deg = bearing_accuracy_deg;
  g.has_fix = has_fix;
  g.event_valid = event_valid;
  g.message_time_ns = message_time_ns;
  return g;
}

// 车体系样本（deviceMotion 经 R^T 后的分量）；source_ok 由组装层给出
static VehicleSample make_vehicle(float forward_speed_m_per_s, float lateral_speed_m_per_s,
                                  float yaw_rate_rad_per_s,
                                  bool yaw_ok, bool speed_ok, uint64_t message_time_ns) {
  VehicleSample v;
  v.forward_speed_m_per_s = forward_speed_m_per_s;
  v.lateral_speed_m_per_s = lateral_speed_m_per_s;
  v.yaw_rate_rad_per_s = yaw_rate_rad_per_s;
  v.reversing = forward_speed_m_per_s < -0.3f;
  v.standstill = std::hypot(forward_speed_m_per_s, lateral_speed_m_per_s) < 0.1f;
  v.yaw_source_ok = yaw_ok;
  v.speed_source_ok = speed_ok;
  v.message_time_ns = message_time_ns;
  return v;
}

static DeviceMotionFlags make_dm_flags(bool event_valid = true, bool sensors_ok = true,
                                       bool inputs_ok = true, bool posenet_ok = true,
                                       ExtrinsicsCalibStatus calib = ExtrinsicsCalibStatus::kCalibrated,
                                       uint64_t message_time_ns = 0) {
  DeviceMotionFlags f;
  f.event_valid = event_valid;
  f.sensors_ok = sensors_ok;
  f.inputs_ok = inputs_ok;
  f.posenet_ok = posenet_ok;
  f.calib_status = calib;
  f.message_time_ns = message_time_ns;
  return f;
}

// 正常 fix：单位换算 + age + 航向精度
static void test_meta_gnss_fix() {
  MetaProvider mp;
  const uint64_t t_eof = 10'000'000'000ULL;
  mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, t_eof - 150'000'000ULL));
  mp.set_vehicle(make_vehicle(12.5f, 0.f, 0.25f, true, true, t_eof - 5'000'000ULL));

  bgm1::FrameHeader h = fill_wire(mp, t_eof);
  CHECK(h.gnss.has_fix());
  CHECK(h.gnss.age_ms() == 150);
  CHECK(h.gnss.latitude_deg_x1e7 == 375000000);
  CHECK(h.gnss.longitude_deg_x1e7 == -1222500000);
  CHECK(h.gnss.speed_valid() && h.gnss.speed_cm_per_s == 1250);
  CHECK(h.gnss.bearing_valid() && h.gnss.bearing_centideg == 9000);
  CHECK(h.gnss.horizontal_accuracy_dm == 125);
  CHECK(h.vehicle.bearing_accuracy_half_deg == 3);  // 1.5° / 0.5° = 3
  CHECK(h.vehicle.yaw_valid());
  CHECK(h.vehicle.yaw_rate_centideg_per_s == 1432);  // 0.25 rad/s = 14.3239 °/s
  CHECK(!h.vehicle.reversing() && !h.vehicle.standstill());
}

// B3：age 是「上次 fix」的年龄；has_fix = 最新消息 hasFix ∧ age ≤ 2000；
// 从未有过 fix 但 speed_valid=1 时 age=4095。
static void test_meta_gnss_no_fix_and_age() {
  const uint64_t t_eof = 10'000'000'000ULL;

  {  // 从未有过 fix、只有车速：age=4095，位置类线上为 0
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(12.5f, 0.f, 0.f, true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.gnss.has_fix() && h.gnss.age_ms() == 4095);
    CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.longitude_deg_x1e7 == 0 && h.gnss.bearing_centideg == 0 && h.gnss.horizontal_accuracy_dm == 0);
    CHECK(h.gnss.speed_valid() && h.gnss.speed_cm_per_s == 1250);
    CHECK(h.vehicle.bearing_accuracy_half_deg == 255);
  }

  {  // 边界：上次 fix 在 2000 ms 前，最新消息 hasFix=true → 仍算有 fix
    MetaProvider mp;
    mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, t_eof - 2'000'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.has_fix() && h.gnss.age_ms() == 2000);
    CHECK(h.gnss.latitude_deg_x1e7 == 375000000 && h.gnss.horizontal_accuracy_dm == 125);
  }

  {  // 边界外：2001 ms → has_fix 0，位置类清 0，age 保留
    MetaProvider mp;
    mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, t_eof - 2'001'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.gnss.has_fix() && h.gnss.age_ms() == 2001);
    CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.longitude_deg_x1e7 == 0 && h.gnss.bearing_centideg == 0 && h.gnss.horizontal_accuracy_dm == 0);
    CHECK(h.vehicle.bearing_accuracy_half_deg == 255);
  }

  {  // 500 ms 前有过 fix，随后一条 hasFix=false：age 仍取上次 fix（500），但 has_fix=0
    MetaProvider mp;
    mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, t_eof - 500'000'000ULL));
    mp.set_gnss(make_gnss(0, 0, 0, 0, 0, false, true, t_eof - 50'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.gnss.has_fix() && h.gnss.age_ms() == 500);
    CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.longitude_deg_x1e7 == 0);
  }

  {  // 极旧 fix：age 饱和 4095，has_fix=0
    MetaProvider mp;
    mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, 0));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.gnss.has_fix() && h.gnss.age_ms() == 4095);
  }

  {  // m9：gnss.message_time_ns > t_eof（消息比帧新）→ age=0，仍算 fix
    MetaProvider mp;
    mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, t_eof + 5'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.has_fix() && h.gnss.age_ms() == 0);
  }

  {  // m9：完全没有数据 → 线上 GNSS 16 B 全 0，VEHICLE 缺省
    MetaProvider mp;
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.longitude_deg_x1e7 == 0 && h.gnss.speed_cm_per_s == 0 &&
          h.gnss.bearing_centideg == 0 && h.gnss.horizontal_accuracy_dm == 0 && h.gnss.fix_age_and_valid_flags == 0);
    CHECK(!h.gnss.has_fix() && !h.gnss.speed_valid() && !h.gnss.bearing_valid());
    CHECK(h.vehicle.yaw_rate_centideg_per_s == bgm1::kYawRateInvalidCentidegPerS && h.vehicle.vehicle_state_flags == 0 &&
          h.vehicle.bearing_accuracy_half_deg == 255);
  }

  {  // gps event_valid=false：不算 fix，也不更新「上次 fix」
    MetaProvider mp;
    mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, false, t_eof - 50'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.gnss.has_fix() && h.gnss.age_ms() == 0);
    CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.speed_valid() == false);
  }
}

// m2/m3 与量程边界：horizontal_accuracy、bearing_accuracy_half_deg、经纬度范围、bearing 有限性、量程饱和
static void test_meta_gnss_saturation() {
  const uint64_t t_eof = 10'000'000'000ULL;
  const VehicleSample veh = make_vehicle(0.f, 0.f, 0.f, true, true, t_eof);

  {  // horizontal_accuracy：≤0/NaN → 0xFFFF；0<m<0.05 → 1 dm；超量程 → 0xFFFE
    MetaProvider mp;
    mp.set_vehicle(veh);
    mp.set_gnss(make_gnss(0, 0, 0, 0.0, 0, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.horizontal_accuracy_dm == 0xFFFF);
    mp.set_gnss(make_gnss(0, 0, 0, 0.04, 0, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.horizontal_accuracy_dm == 1);
    mp.set_gnss(make_gnss(0, 0, 0, std::nan(""), 0, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.horizontal_accuracy_dm == 0xFFFF);
    mp.set_gnss(make_gnss(0, 0, 0, 10000.0, 0, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.horizontal_accuracy_dm == 0xFFFE);
  }

  {  // bearing_accuracy_half_deg：≤0/NaN → 255；0<x<0.25 → 1；饱和 254
    MetaProvider mp;
    mp.set_vehicle(veh);
    mp.set_gnss(make_gnss(0, 0, 0, 1, 0.0, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.bearing_accuracy_half_deg == 255);
    mp.set_gnss(make_gnss(0, 0, 0, 1, 0.1, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.bearing_accuracy_half_deg == 1);
    mp.set_gnss(make_gnss(0, 0, 0, 1, std::nan(""), true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.bearing_accuracy_half_deg == 255);
    mp.set_gnss(make_gnss(0, 0, 0, 1, 1000.0, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.bearing_accuracy_half_deg == 254);
  }

  {  // m3：|lat| ≤ 90、|lon| ≤ 180 才有 fix；负纬度、±90/±180 合法
    MetaProvider mp;
    mp.set_gnss(make_gnss(90.0, 180.0, 1, 1, 1, true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.has_fix() && h.gnss.latitude_deg_x1e7 == 900000000 && h.gnss.longitude_deg_x1e7 == 1800000000);
    mp.set_gnss(make_gnss(-90.0, -180.0, 1, 1, 1, true, true, t_eof));
    h = fill_wire(mp, t_eof);
    CHECK(h.gnss.has_fix() && h.gnss.latitude_deg_x1e7 == -900000000 && h.gnss.longitude_deg_x1e7 == -1800000000);
    mp.set_gnss(make_gnss(91.0, 0, 1, 1, 1, true, true, t_eof));
    CHECK(!fill_wire(mp, t_eof).gnss.has_fix());
    mp.set_gnss(make_gnss(-90.1, 0, 1, 1, 1, true, true, t_eof));
    CHECK(!fill_wire(mp, t_eof).gnss.has_fix());
    mp.set_gnss(make_gnss(0, 181.0, 1, 1, 1, true, true, t_eof));
    CHECK(!fill_wire(mp, t_eof).gnss.has_fix());
    mp.set_gnss(make_gnss(std::nan(""), 0, 1, 1, 1, true, true, t_eof));
    CHECK(!fill_wire(mp, t_eof).gnss.has_fix());
  }

  {  // bearing 非有限 → bearing_valid=0（位置仍有 fix）；回绕到 [0, 36000)
    MetaProvider mp;
    mp.set_gnss(make_gnss(0, 0, std::nan(""), 1, 1, true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.has_fix() && !h.gnss.bearing_valid() && h.gnss.bearing_centideg == 0);
    mp.set_gnss(make_gnss(0, 0, 359.999, 1, 1, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.bearing_centideg == 0);
    mp.set_gnss(make_gnss(0, 0, -10.0, 1, 1, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.bearing_centideg == 35000);
  }

  {  // 无 GNSS、只有车速：speed/speed_valid 独立于定位；其余为 0，age=4095
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(8.0f, 0.f, 0.f, true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.gnss.has_fix() && h.gnss.age_ms() == 4095);
    CHECK(h.gnss.speed_valid() && h.gnss.speed_cm_per_s == 800);
    CHECK(h.gnss.latitude_deg_x1e7 == 0 && h.gnss.longitude_deg_x1e7 == 0);
    CHECK(h.vehicle.bearing_accuracy_half_deg == 255);
  }

  {  // 车体系前向速度为负 → speed 取模长，reversing 位置位；阈值严格 < -0.3
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(-8.0f, 0.f, 0.f, true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.speed_valid() && h.gnss.speed_cm_per_s == 800);
    CHECK(h.vehicle.reversing());
    mp.set_vehicle(make_vehicle(-0.3f, 0.f, 0.f, true, true, t_eof));
    CHECK(!fill_wire(mp, t_eof).vehicle.reversing());
    mp.set_vehicle(make_vehicle(-0.31f, 0.f, 0.f, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.reversing());
  }

  {  // standstill：水平模长 < 0.1 m/s
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(0.06f, 0.07f, 0.f, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.standstill());
    mp.set_vehicle(make_vehicle(0.08f, 0.08f, 0.f, true, true, t_eof));
    CHECK(!fill_wire(mp, t_eof).vehicle.standstill());
  }
}

// deviceMotion 有效性：100 ms 内（含）+ event_valid/sensorsOK + 有限值；yaw 与 speed 各自判
static void test_meta_vehicle_device_motion() {
  const uint64_t t_eof = 10'000'000'000ULL;

  {  // 正常样本
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(10.f, 0.f, 0.25f, true, true, t_eof - 5'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.vehicle.yaw_valid() && h.vehicle.yaw_rate_centideg_per_s == 1432);
    CHECK(h.gnss.speed_valid() && h.gnss.speed_cm_per_s == 1000);
  }
  {  // 100 ms 边界内有效；超出 1 ns 无效
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(10.f, 0.f, 0.25f, true, true, t_eof - 100'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.vehicle.yaw_valid() && h.gnss.speed_valid());
    mp.set_vehicle(make_vehicle(10.f, 0.f, 0.25f, true, true, t_eof - 100'000'001ULL));
    h = fill_wire(mp, t_eof);
    CHECK(!h.vehicle.yaw_valid() && !h.gnss.speed_valid());
    CHECK(h.vehicle.yaw_rate_centideg_per_s == bgm1::kYawRateInvalidCentidegPerS && h.gnss.speed_cm_per_s == 0);
  }
  {  // 明确 101 ms：yaw 与 speed 都无效
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(10.f, 0.f, 0.25f, true, true, t_eof - 101'000'000ULL));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.vehicle.yaw_valid() && !h.gnss.speed_valid());
  }
  {  // 消息比 t_eof 新也算新鲜
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(10.f, 0.f, 0.25f, true, true, t_eof + 5'000'000ULL));
    CHECK(fill_wire(mp, t_eof).vehicle.yaw_valid());
  }
  {  // 组装层判出 source_ok=false（event_valid / sensorsOK 任一 false）→ 两个 valid 位都 0
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(10.f, 0.f, 0.25f, false, false, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.vehicle.yaw_valid() && !h.gnss.speed_valid());
  }
  {  // 分量 NaN：yaw NaN 只废 yaw；速度 NaN 只废 speed
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(10.f, 0.f, std::nanf(""), true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.vehicle.yaw_valid() && h.vehicle.yaw_rate_centideg_per_s == bgm1::kYawRateInvalidCentidegPerS);
    CHECK(h.gnss.speed_valid());
    mp.set_vehicle(make_vehicle(std::nanf(""), 0.f, 0.25f, true, true, t_eof));
    h = fill_wire(mp, t_eof);
    CHECK(h.vehicle.yaw_valid() && !h.gnss.speed_valid());
  }
  {  // yaw ±32767 饱和（避开 INT16_MIN）
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(1.f, 0.f, 100.0f, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.yaw_rate_centideg_per_s == 32767);
    mp.set_vehicle(make_vehicle(1.f, 0.f, -100.0f, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).vehicle.yaw_rate_centideg_per_s == -32767);
  }
  {  // speed 65535 饱和；负速度已在上面的 reversing 用例覆盖
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(1000.f, 0.f, 0.f, true, true, t_eof));
    CHECK(fill_wire(mp, t_eof).gnss.speed_cm_per_s == 65535);
  }
  {  // speed 用前向分量：横向 NaN 不废 speed，但算不出 standstill
    MetaProvider mp;
    mp.set_vehicle(make_vehicle(1.f, std::nanf(""), 0.f, true, true, t_eof));
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(h.gnss.speed_valid() && h.gnss.speed_cm_per_s == 100);
    CHECK(!h.vehicle.standstill());
  }
  {  // 无 deviceMotion：yaw/speed 都无效，整块 VEHICLE 缺省
    MetaProvider mp;
    bgm1::FrameHeader h = fill_wire(mp, t_eof);
    CHECK(!h.vehicle.yaw_valid() && !h.gnss.speed_valid());
    CHECK(h.vehicle.vehicle_state_flags == 0 && h.vehicle.bearing_accuracy_half_deg == 255);
  }
}

// 车体系换算与 Python rot_from_euler 数值对照（rpy=[0.01,-0.02,0.03]）
static void test_device_motion_transform() {
  const float rpy[3] = {0.01f, -0.02f, 0.03f};
  const float vel[3] = {5.0f, 1.0f, 0.2f};
  const float ang[3] = {0.1f, -0.2f, 0.3f};
  const DeviceMotionFlags flags = make_dm_flags(true, true, true, true,
                                                ExtrinsicsCalibStatus::kCalibrated, 123);

  VehicleSample s = vehicle_sample_from_device_motion(rpy, vel, ang, flags);
  CHECK(s.message_time_ns == 123 && s.yaw_source_ok && s.speed_source_ok);
  CHECK_NEAR(s.forward_speed_m_per_s, 5.03073988667, 1e-5);
  CHECK_NEAR(s.lateral_speed_m_per_s, 0.85052415581, 1e-5);
  CHECK_NEAR(s.yaw_rate_rad_per_s, 0.300075168029, 1e-5);
  CHECK(!s.reversing && !s.standstill);

  // 车体系判据：倒车向量 → reversing；零速 → standstill
  const float back[3] = {-1.0f, 0.0f, 0.0f};
  const float zero[3] = {0.0f, 0.0f, 0.0f};
  s = vehicle_sample_from_device_motion(rpy, back, ang, flags);
  CHECK(s.reversing && !s.standstill);
  s = vehicle_sample_from_device_motion(rpy, zero, ang, flags);
  CHECK(!s.reversing && s.standstill);

  // 分量非有限 → 对应 source_ok false
  const float bad_v[3] = {std::nanf(""), 0.f, 0.f};
  const float bad_w[3] = {0.f, 0.f, std::nanf("")};
  s = vehicle_sample_from_device_motion(rpy, bad_v, ang, flags);
  CHECK(!s.speed_source_ok && s.yaw_source_ok);
  s = vehicle_sample_from_device_motion(rpy, vel, bad_w, flags);
  CHECK(s.speed_source_ok && !s.yaw_source_ok);
}

// S1：yaw 物理语义——rpy=0、ω_device=(0,0,+0.1) 表示从上往下看顺时针（右转），
// 与 bearing 同向；+z 为车体系 FRD 的 z 轴（向下）。车机取 w_body.z 不变，只改注释。
static void test_yaw_sign_convention() {
  const float rpy0[3] = {0.f, 0.f, 0.f};
  const float rpy_mount[3] = {0.f, 0.f, 0.05f};  // yaw 安装角
  const float zero[3] = {0.f, 0.f, 0.f};
  const float w_right[3] = {0.f, 0.f, 0.1f};   // +z：顺时针（右转）
  const float w_left[3] = {0.f, 0.f, -0.1f};   // −z：逆时针（左转）
  const DeviceMotionFlags flags = make_dm_flags(true, true, true, true,
                                                ExtrinsicsCalibStatus::kCalibrated, 1000);

  MetaProvider mp;
  VehicleSample s = vehicle_sample_from_device_motion(rpy0, zero, w_right, flags);
  CHECK_NEAR(s.yaw_rate_rad_per_s, 0.1, 1e-6);
  mp.set_vehicle(s);
  bgm1::FrameHeader h = fill_wire(mp, 1000);
  CHECK(h.vehicle.yaw_valid() && h.vehicle.yaw_rate_centideg_per_s == 573);  // 0.1 rad/s = +5.7296 °/s

  s = vehicle_sample_from_device_motion(rpy0, zero, w_left, flags);
  CHECK_NEAR(s.yaw_rate_rad_per_s, -0.1, 1e-6);
  mp.set_vehicle(s);
  h = fill_wire(mp, 1000);
  CHECK(h.vehicle.yaw_valid() && h.vehicle.yaw_rate_centideg_per_s == -573);

  // 非零 yaw 安装角：纯 z 轴旋转不改变 z 分量，符号仍为正
  s = vehicle_sample_from_device_motion(rpy_mount, zero, w_right, flags);
  CHECK_NEAR(s.yaw_rate_rad_per_s, 0.1, 1e-6);
  mp.set_vehicle(s);
  CHECK(fill_wire(mp, 1000).vehicle.yaw_rate_centideg_per_s == 573);
}

// I1 / m-b：speed = event_valid ∧ sensorsOK ∧ inputsOK ∧ posenetOK ∧ 有限；
// yaw = event_valid ∧ sensorsOK ∧ 有限（不看 inputsOK/posenetOK）；calStatus==invalid 两者都无效。
static void test_device_motion_validity_flags() {
  const float rpy[3] = {0.f, 0.f, 0.f};
  const float vel[3] = {5.f, 0.f, 0.f};
  const float ang[3] = {0.f, 0.f, 0.1f};

  for (bool inputs : {true, false}) {
    VehicleSample s = vehicle_sample_from_device_motion(
        rpy, vel, ang, make_dm_flags(true, true, inputs, true));
    CHECK(s.yaw_source_ok == true);
    CHECK(s.speed_source_ok == inputs);
  }
  for (bool posenet : {true, false}) {
    VehicleSample s = vehicle_sample_from_device_motion(
        rpy, vel, ang, make_dm_flags(true, true, true, posenet));
    CHECK(s.yaw_source_ok == true);
    CHECK(s.speed_source_ok == posenet);
  }
  // event_valid / sensorsOK 任一 false → yaw 与 speed 都无效
  for (bool event_valid : {true, false}) {
    VehicleSample s = vehicle_sample_from_device_motion(
        rpy, vel, ang, make_dm_flags(event_valid, true, true, true));
    CHECK(s.yaw_source_ok == event_valid && s.speed_source_ok == event_valid);
  }
  for (bool sensors : {true, false}) {
    VehicleSample s = vehicle_sample_from_device_motion(
        rpy, vel, ang, make_dm_flags(true, sensors, true, true));
    CHECK(s.yaw_source_ok == sensors && s.speed_source_ok == sensors);
  }

  // m-b：invalid 两者都无效；uncalibrated / calibrated / recalibrating 照常
  using CS = ExtrinsicsCalibStatus;
  for (CS st : {CS::kUncalibrated, CS::kCalibrated, CS::kRecalibrating}) {
    VehicleSample s = vehicle_sample_from_device_motion(
        rpy, vel, ang, make_dm_flags(true, true, true, true, st));
    CHECK(s.yaw_source_ok && s.speed_source_ok);
  }
  VehicleSample s_invalid = vehicle_sample_from_device_motion(
      rpy, vel, ang, make_dm_flags(true, true, true, true, CS::kInvalid));
  CHECK(!s_invalid.yaw_source_ok && !s_invalid.speed_source_ok);

  // 线上复核：I1 的 inputsOK/posenetOK 只废 speed；calStatus=invalid 两者都废
  const uint64_t t_eof = 10'000'000'000ULL;
  const DeviceMotionFlags f_inputs =
      make_dm_flags(true, true, false, true, CS::kCalibrated, t_eof);
  MetaProvider mp;
  mp.set_vehicle(vehicle_sample_from_device_motion(rpy, vel, ang, f_inputs));
  bgm1::FrameHeader h = fill_wire(mp, t_eof);
  CHECK(h.vehicle.yaw_valid() && !h.gnss.speed_valid());

  const DeviceMotionFlags f_posenet =
      make_dm_flags(true, true, true, false, CS::kCalibrated, t_eof);
  mp.set_vehicle(vehicle_sample_from_device_motion(rpy, vel, ang, f_posenet));
  h = fill_wire(mp, t_eof);
  CHECK(h.vehicle.yaw_valid() && !h.gnss.speed_valid());

  const DeviceMotionFlags f_invalid =
      make_dm_flags(true, true, true, true, CS::kInvalid, t_eof);
  mp.set_vehicle(vehicle_sample_from_device_motion(rpy, vel, ang, f_invalid));
  h = fill_wire(mp, t_eof);
  CHECK(!h.vehicle.yaw_valid() && !h.gnss.speed_valid());
}

// m-a：按真实因果链重写相位测试。
//   t_eof(k) = base + k·50 ms
//   modelDataV2SP 在 eof+M 唤醒 calib_thread（M = 30/45/60 ms，覆盖 bigModel REPLY 迟到范围）
//   deviceMotion 在 eof+M+ε 发布（ε = 1..3 ms）→ 唤醒时读到上一帧样本
//   打包在 eof+L（L = 3/5/10 ms）
// 稳态下 yaw_valid 与 speed_valid 恒为 1。额外注入「第 40 帧 modelDataV2SP 迟到 60 ms
// 或丢一帧」；丢帧时该帧的 deviceMotion（依赖 cameraOdometry）一并消失。
// 断言无效帧 ≤2、之后恢复。窗口若改回 20 ms，本测试会大量红。
struct PhaseResult {
  int invalid = 0;
  int max_run = 0;
  int invalid_last10 = 0;
};

static PhaseResult run_phase(uint64_t M, uint64_t eps, uint64_t L, int anomaly,
                             bool alternate_m = false) {
  constexpr uint64_t P = 50'000'000;  // 20 Hz
  constexpr uint64_t base = 5'000'000'000ULL;
  constexpr int N = 80;
  constexpr int kAnomalyFrame = 40;

  std::vector<uint64_t> dms, wakes;
  for (int k = 0; k < N; k++) {
    const uint64_t Mk = alternate_m ? ((k % 2) ? 60'000'000ULL : 30'000'000ULL) : M;
    if (anomaly == 1 && k == kAnomalyFrame) continue;  // 丢帧：modelDataV2SP + deviceMotion 一起消失
    dms.push_back(base + uint64_t(k) * P + Mk + eps);
    wakes.push_back(base + uint64_t(k) * P + Mk);
  }
  if (anomaly == 2) wakes[kAnomalyFrame] += 60'000'000;  // 迟到 60 ms
  std::sort(wakes.begin(), wakes.end());

  MetaProvider mp;
  size_t wi = 0, di = 0;
  uint64_t last_dm = 0, sample_t = 0;
  bool have_dm = false, have_sample = false;
  PhaseResult r;
  int run = 0;

  for (int k = 0; k < N; k++) {
    const uint64_t eof = base + uint64_t(k) * P;
    const uint64_t pack = eof + L;
    // 按时间序处理打包前的唤醒与 deviceMotion（唤醒只消费已到达的最新一条）
    for (;;) {
      const bool wake_ready = wi < wakes.size() && wakes[wi] <= pack;
      const bool dm_ready = di < dms.size() && dms[di] <= pack;
      if (!wake_ready && !dm_ready) break;
      if (dm_ready && (!wake_ready || dms[di] <= wakes[wi])) {
        last_dm = dms[di];
        have_dm = true;
        di++;
      } else {
        if (have_dm && last_dm <= wakes[wi]) {
          sample_t = last_dm;
          have_sample = true;
        }
        wi++;
      }
    }
    if (have_sample) {
      mp.set_vehicle(make_vehicle(10.f, 0.f, 0.1f, true, true, sample_t));
    }
    bgm1::FrameHeader h = fill_wire(mp, eof);
    const bool ok = h.vehicle.yaw_valid() && h.gnss.speed_valid();
    if (k >= 3) {
      if (!ok) {
        r.invalid++;
        run++;
        r.max_run = std::max(r.max_run, run);
        if (k >= N - 10) r.invalid_last10++;
      } else {
        run = 0;
      }
    }
  }
  return r;
}

static void test_meta_vehicle_phase() {
  // 稳态：M 覆盖 30/45/60 ms（bigModel REPLY 截止 60 ms），全部相位组合恒有效
  for (uint64_t M : {30'000'000ULL, 45'000'000ULL, 60'000'000ULL}) {
    for (uint64_t eps : {1'000'000ULL, 2'000'000ULL, 3'000'000ULL}) {
      for (uint64_t L : {3'000'000ULL, 5'000'000ULL, 10'000'000ULL}) {
        PhaseResult r = run_phase(M, eps, L, 0);
        CHECK(r.invalid == 0);
      }
    }
  }

  // 交替 M=30/60 ms：紧跟在 60 ms 帧之后的一帧允许无效；统计实际占比
  int alt_invalid = 0;
  int alt_frames = 0;
  for (uint64_t eps : {1'000'000ULL, 3'000'000ULL}) {
    for (uint64_t L : {3'000'000ULL, 10'000'000ULL}) {
      PhaseResult r = run_phase(0, eps, L, 0, true);
      CHECK(r.invalid <= 1);
      CHECK(r.invalid_last10 == 0);
      alt_invalid += r.invalid;
      alt_frames += 77;  // 计 k=3..79
    }
  }
  printf("phase alternate M=30/60: %d/%d invalid (%.1f%%)\n", alt_invalid, alt_frames,
         alt_frames ? 100.0 * alt_invalid / alt_frames : 0.0);

  // 异常：迟到 60 ms / 丢一帧（deviceMotion 一起去掉）→ 无效帧 ≤2，且之后恢复
  for (uint64_t M : {30'000'000ULL, 45'000'000ULL, 60'000'000ULL}) {
    for (uint64_t eps : {1'000'000ULL, 3'000'000ULL}) {
      for (uint64_t L : {3'000'000ULL, 10'000'000ULL}) {
        for (int anomaly : {1, 2}) {
          PhaseResult r = run_phase(M, eps, L, anomaly);
          CHECK(r.invalid <= 2);
          CHECK(r.max_run <= 2);
          CHECK(r.invalid_last10 == 0);
        }
      }
    }
  }
}

// fill 路径零堆分配：全局 operator new 计数在 1000 次 fill 前后不变
static void test_meta_fill_zero_alloc() {
  MetaProvider mp;
  const uint64_t t_eof = 10'000'000'000ULL;
  mp.set_gnss(make_gnss(37.5, -122.25, 90.0, 12.5, 1.5, true, true, t_eof));
  mp.set_vehicle(make_vehicle(12.5f, 0.f, 0.25f, true, true, t_eof));

  // 计数器自检：一次 4 KiB 分配必须被记到，避免覆盖未生效导致空测
  const uint64_t self = g_alloc_count.load(std::memory_order_relaxed);
  { std::vector<uint8_t> probe(4096, 1); CHECK(probe.size() == 4096); }
  CHECK(g_alloc_count.load(std::memory_order_relaxed) > self);

  bgm1::FrameHeader h;
  const uint64_t before = g_alloc_count.load(std::memory_order_relaxed);
  for (int i = 0; i < 1000; i++) mp.fill(t_eof + uint64_t(i) * 50'000'000ULL, &h);
  CHECK(g_alloc_count.load(std::memory_order_relaxed) == before);
}

// ---- 配对状态机（16 号诊断修复：按 timestamp_sof 配对，不按 frame_id）----
using PM = bgm::PairMatcher<int>;

// 配对杀帧只记账：road SOF 进入时间槽编号，但不送入调度器开新序列。
static void test_pair_kill_does_not_recover() {
  FrameScheduler sched;
  sched.on_connect();
  FrameIndexer indexer;
  PM matcher;
  const uint64_t t0 = 4000000000ULL;

  matcher.push(true, {1, t0, 0});
  PM::Actions first = matcher.push(false, {5001, t0 + 70000, 1});
  CHECK(first.pair);
  SchedStep first_submit = sched.on_frame_submit(indexer.index(first.road.timestamp_sof));
  CHECK(first_submit.road_idr && first_submit.wide_idr);

  matcher.push(true, {2, t0 + 50000000ULL, 2});
  PM::Actions killed = matcher.push(true, {3, t0 + 100000000ULL, 3});
  CHECK(killed.kill_road && killed.road_dead.frame_id == 2);
  // 对照 main.cc::place_frame：只为统计/日志编号，不调用 sched.on_frame_dropped。
  CHECK(indexer.index(killed.road_dead.timestamp_sof) == FrameIdx{1});

  PM::Actions next = matcher.push(false, {5003, t0 + 100070000ULL, 4});
  CHECK(next.pair);
  const FrameIdx next_idx = indexer.index(next.road.timestamp_sof);
  CHECK(u32(next_idx) == 2);
  SchedStep next_submit = sched.on_frame_submit(FrameIdx{next_idx});
  CHECK(!next_submit.any());  // 配对杀帧没有插入新序列/request
}

// bug 最小复现（真机 2026-09-28：两路 frame_id 恒差 3372 且持续漂移）：
// frame_id 不等但 SOF 对齐 → 必须成对
static void test_pair_off_fid_aligned_sof() {
  PM m;
  uint64_t t = 1000000000ULL;
  for (int k = 0; k < 5; k++) {
    PM::Actions a1 = m.push(true, {100 + (uint32_t)k, t + 50000000ULL * k, k});
    CHECK(!a1.pair);
    PM::Actions a2 = m.push(false, {5000 + (uint32_t)k, t + 50000000ULL * k + 70000, k});
    CHECK(a2.pair);
    CHECK(a2.road.frame_id == 100 + (uint32_t)k);
    CHECK(a2.wide.frame_id == 5000 + (uint32_t)k);
    CHECK(!a2.kill_road && !a2.kill_wide);
  }
}

// 反例锁定：frame_id 相等不是配对键，SOF 错开 30 ms 不许成对（且旧帧超窗驱逐）
static void test_pair_same_fid_offset_sof() {
  PM m;
  PM::Actions a1 = m.push(true, {7, 1000000000ULL, 0});
  CHECK(!a1.pair);
  PM::Actions a2 = m.push(false, {7, 1000000000ULL + 30000000ULL, 1});
  CHECK(!a2.pair);
  CHECK(a2.kill_road && a2.road_dead.frame_id == 7);
  CHECK(!a2.kill_wide);
}

// 容差边界：10 ms 成对、11 ms 不成对
static void test_pair_tolerance_boundary() {
  PM m;
  m.push(true, {1, 5000000000ULL, 0});
  PM::Actions a = m.push(false, {2, 5000000000ULL + bgm::kPairToleranceNs, 1});
  CHECK(a.pair);

  PM m2;
  m2.push(true, {1, 5000000000ULL, 0});
  PM::Actions b = m2.push(false, {2, 5000000000ULL + bgm::kPairToleranceNs + 1, 1});
  CHECK(!b.pair);
  CHECK(b.kill_road && b.road_dead.frame_id == 1);  // road 超窗驱逐
}

// 置换杀旧（对路缺帧）+ wide 侧死亡与 road 侧死亡分开标记
static void test_pair_replace_and_evict() {
  PM m;
  uint64_t t = 2000000000ULL;
  m.push(true, {10, t, 0});
  PM::Actions a = m.push(true, {11, t + 50000000ULL, 1});  // road 连发，wide 缺帧
  CHECK(!a.pair);
  CHECK(a.kill_road && a.road_dead.frame_id == 10);
  CHECK(!a.kill_wide);

  PM::Actions b = m.push(true, {12, t + 100000000ULL, 2});
  CHECK(b.kill_road && b.road_dead.frame_id == 11);

  // wide 侧死亡只标 kill_wide（不上报调度器）
  PM m2;
  m2.push(false, {20, t, 10});
  PM::Actions c = m2.push(false, {21, t + 50000000ULL, 11});  // wide 连发 → 置换杀旧
  CHECK(c.kill_wide && c.wide_dead.frame_id == 20 && !c.kill_road);
  PM::Actions d = m2.push(true, {30, t + 100000000ULL, 12});  // road 晚到 → wide 21 超窗驱逐
  CHECK(d.kill_wide && d.wide_dead.frame_id == 21 && !d.kill_road && !d.pair);
}

// 同步组仿真（真机形态）：两路 SOF 同 50 ms 网格对齐（stagger 70 µs），各自跳过
// 不同槽位（road 每 37 槽缺 1、wide 每 12 槽缺 1）。断言：同槽成对恰好一次、
// 缺槽单侧死亡、每帧恰好退出一次、无错配。
static void test_pair_rate_mismatch_sim() {
  const int N = 120;
  const uint64_t t0 = 3000000000ULL, period = 50000000ULL;
  auto road_has = [](int k) { return k % 37 != 0; };
  auto wide_has = [](int k) { return k % 12 != 0; };

  PM m;
  std::vector<std::pair<uint32_t, uint32_t>> pairs;
  std::vector<uint32_t> dead;
  int expected_pairs = 0;

  for (int k = 0; k < N; k++) {
    if (road_has(k) && wide_has(k)) expected_pairs++;
    // 到达顺序 = SOF 顺序：road 先（stagger 早 70 µs）
    if (road_has(k)) {
      PM::Actions a = m.push(true, {(uint32_t)(100 + k), t0 + period * k, k});
      if (a.pair) pairs.push_back({a.road.frame_id, a.wide.frame_id});
      if (a.kill_road) dead.push_back(a.road_dead.frame_id);
      if (a.kill_wide) dead.push_back(a.wide_dead.frame_id);
    }
    if (wide_has(k)) {
      PM::Actions a = m.push(false, {(uint32_t)(5000 + k), t0 + period * k + 70000, 1000 + k});
      if (a.pair) pairs.push_back({a.road.frame_id, a.wide.frame_id});
      if (a.kill_road) dead.push_back(a.road_dead.frame_id);
      if (a.kill_wide) dead.push_back(a.wide_dead.frame_id);
    }
  }

  CHECK((int)pairs.size() == expected_pairs);  // 同槽全配上，无错配无漏配
  int total = 0;
  for (int k = 0; k < N; k++) total += (road_has(k) ? 1 : 0) + (wide_has(k) ? 1 : 0);
  int leftover = (m.has_pending(true) ? 1 : 0) + (m.has_pending(false) ? 1 : 0);
  CHECK(2 * (int)pairs.size() + (int)dead.size() + leftover == total);  // 每帧恰好退出一次

  std::vector<uint32_t> used;
  for (auto& p : pairs) {
    used.push_back(p.first);
    used.push_back(p.second);
  }
  used.insert(used.end(), dead.begin(), dead.end());
  std::sort(used.begin(), used.end());
  CHECK(std::unique(used.begin(), used.end()) == used.end());  // 无重复使用/重复杀死
}

// =====================================================================
// MetaCache（18 号 clean2 根因）：查不到不弹队、miss 分类
// =====================================================================

static OutMeta mk_om(uint32_t frame_id, uint32_t frame_idx) {
  OutMeta om;
  om.frame_id = CamFrameId{frame_id};
  om.frame_idx = FrameIdx{frame_idx};
  return om;
}

// clean2 回归（2026-09-28）：meta.clear()（重连）后旧连接输出迟到查不到键——
// 绝不许吃掉幸存的新条目（序列头双路 IDR 对曾因此整对消失）
static void test_meta_cache_clear_then_stale_take() {
  MetaCache m;
  m.push(mk_om(100, 0));  // 旧连接在途
  m.clear();              // kNewConnection：清空
  m.push(mk_om(200, 0));  // 新序列头对（seq0）
  m.push(mk_om(201, 1));

  MetaCache::Result r = m.take(CamFrameId{100});  // 旧输出迟到
  CHECK(r.st == MetaTake::kStaleMiss && r.dead.empty());

  r = m.take(CamFrameId{200});  // 头对必须还在
  CHECK(r.st == MetaTake::kHit && r.om.frame_id == CamFrameId{200} && r.om.frame_idx == FrameIdx{0});
  r = m.take(CamFrameId{201});
  CHECK(r.st == MetaTake::kHit && r.om.frame_id == CamFrameId{201});
}

// clear 后尚无新提交就来旧输出：kStaleMiss（不是断档）；比 front 旧同理
static void test_meta_cache_stale_classification() {
  MetaCache m;
  m.push(mk_om(50, 3));
  m.clear();
  CHECK(m.take(CamFrameId{50}).st == MetaTake::kStaleMiss);  // clear 后无新提交
  m.push(mk_om(60, 0));
  CHECK(m.take(CamFrameId{59}).st == MetaTake::kStaleMiss);  // 比 front 旧 = 已清/已弹
  CHECK(m.take(CamFrameId{60}).st == MetaTake::kHit);
  CHECK(m.take(CamFrameId{60}).st == MetaTake::kStaleMiss);  // 已弹的迟到输出
}

// 命中前缀死条目 = 输出被吞（断档），随命中返回；查无且非旧 = kGapMiss（断档）
static void test_meta_cache_dead_prefix_and_gap() {
  MetaCache m;
  m.push(mk_om(10, 0));
  m.push(mk_om(11, 1));
  m.push(mk_om(12, 2));
  MetaCache::Result r = m.take(CamFrameId{12});
  CHECK(r.st == MetaTake::kHit && r.om.frame_id == CamFrameId{12});
  CHECK(r.dead.size() == 2 && r.dead[0].frame_id == CamFrameId{10} && r.dead[1].frame_id == CamFrameId{11});

  // 死条目自己的输出迟到：已弹 = kStaleMiss（不重复上报）
  CHECK(m.take(CamFrameId{10}).st == MetaTake::kStaleMiss);

  // 队内比 front 新却查无 = 输出被吞 = 断档
  m.push(mk_om(20, 3));
  CHECK(m.take(CamFrameId{21}).st == MetaTake::kGapMiss);
  // 关键回归：断档 miss 不弹队，幸存条目还在
  r = m.take(CamFrameId{20});
  CHECK(r.st == MetaTake::kHit && r.om.frame_id == CamFrameId{20});
}

// =====================================================================
// 序列头门 + 连接代号（18 号 #1/#2）
// =====================================================================

// P 对不开流：整对丢弃（kHeadBarrier）直到「预测+实测双路 IDR」对上线
static void test_head_barrier_p_pair_dropped() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();
  evs.clear();

  const uint8_t p[2] = {1, 2};
  // P 对（帧 0）：预测非 IDR → 不开流，什么字节都不许出
  s.submit_road(ConnEpoch{1}, FrameIdx{0}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{1}, FrameIdx{0}, p, sizeof p, false);
  s.step();
  s.step();
  CHECK(sock.written.empty());
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kHeadBarrier && evs[0].idx == 0);

  // 预测双 IDR 但实测 P（request 没落上）→ 同样丢弃（实测才是开流条件）
  s.submit_road(ConnEpoch{1}, FrameIdx{1}, test_hdr(), p, sizeof p, false, true, true);
  s.submit_wide(ConnEpoch{1}, FrameIdx{1}, p, sizeof p, false);
  s.step();
  CHECK(sock.written.empty());
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kHeadBarrier && evs[1].idx == 1);

  // 合格双 IDR 对开流
  open_head(s, 1, 2, p, sizeof p, p, sizeof p);
  s.step();
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kFrameSent && evs[2].idx == 2);
  CHECK(sock.written.size() == bgm1::frame_wire_size(sizeof p, sizeof p));
}

// 码流断档（notify_stream_gap）重新关门：断档后首帧必须双路 IDR（18 号 #1）
static void test_head_barrier_after_stream_gap() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();
  evs.clear();

  const uint8_t p[2] = {1, 2};
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);  // 头对开流
  s.step();
  CHECK(evs.size() == 1 && evs[0].ev == UplinkEvent::kFrameSent);

  // 门开着：中流 P 对正常放行（road 出包即发）
  s.submit_road(ConnEpoch{1}, FrameIdx{1}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{1}, FrameIdx{1}, p, sizeof p, false);
  s.step();
  s.step();
  CHECK(evs.size() == 2 && evs[1].ev == UplinkEvent::kFrameSent && evs[1].idx == 1);
  const size_t before = sock.written.size();

  // 主线上报断档（编码输出缺帧/段长超限）→ 关门
  s.notify_stream_gap();
  // 断档后紧跟的 P 对（帧 2）不得成为首帧：整对丢弃
  s.submit_road(ConnEpoch{1}, FrameIdx{2}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{1}, FrameIdx{2}, p, sizeof p, false);
  s.step();
  s.step();
  CHECK(sock.written.size() == before);
  CHECK(evs.size() == 3 && evs[2].ev == UplinkEvent::kHeadBarrier && evs[2].idx == 2);

  // 双 IDR 对（帧 3）重新开流
  open_head(s, 1, 3, p, sizeof p, p, sizeof p);
  s.step();
  CHECK(evs.size() == 4 && evs[3].ev == UplinkEvent::kFrameSent && evs[3].idx == 3);
}

// 连接代号：重连窗口内旧连接的迟到提交静默拒绝，不污染新连接
static void test_sender_conn_epoch_rejects_stale() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();  // 连接代号 1
  CHECK(evs[0].ep == 1 && s.conn_epoch() == ConnEpoch{1});
  evs.clear();

  const uint8_t p[2] = {1, 2};
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);
  s.step();

  s.notify_disconnect();  // 断连
  sock.mode = FakeSock::kWrite;
  CHECK(s.step());  // 重连 → 连接代号 2
  CHECK(evs.back().ev == UplinkEvent::kNewConnection && evs.back().ep == 2);
  const size_t before = sock.written.size();

  // 旧代号提交（重连窗口内旧连接的编码在途输出）→ 静默丢弃
  s.submit_road(ConnEpoch{1}, FrameIdx{5}, test_hdr(), p, sizeof p, true, true, true);
  s.submit_wide(ConnEpoch{1}, FrameIdx{5}, p, sizeof p, true);
  s.step();
  s.step();
  CHECK(sock.written.size() == before);  // 帧 5 不得进新连接
  for (auto& e : evs) CHECK(!(e.ev == UplinkEvent::kFrameSent && e.idx == 5));

  // 新代号头对正常开流（frame_idx 从 0 重新编号，无旧编号污染）
  open_head(s, 2, 0, p, sizeof p, p, sizeof p);
  s.step();
  CHECK(evs.back().ev == UplinkEvent::kFrameSent && evs.back().idx == 0);
}

// stall3 回归（2026-09-29）：已丢帧 tombstone 窗口不得跨连接——frame_idx 每连接重新
// 编号，旧连接的丢帧 id 会静默吃掉新连接的同 id 帧（GOP 相位偏移 + 解码断链，无上报）
static void test_sender_dropped_window_not_cross_connection() {
  FakeSock sock;
  uint64_t now = 0;
  std::vector<Ev> evs;
  UplinkSender s(&sock, [&] { return now; }, UplinkSenderConfig{},
                 [&](const UplinkEventInfo& e) { evs.push_back({e.ev, u32(e.frame_idx), u32(e.conn_epoch)}); });
  s.step();  // 连接代号 1
  evs.clear();

  const uint8_t p[2] = {1, 2};
  open_head(s, 1, 0, p, sizeof p, p, sizeof p);
  s.step();
  CHECK(evs.back().ev == UplinkEvent::kFrameSent && evs.back().idx == 0);
  evs.clear();

  // 旧连接制造 tombstone：帧 1 进排队槽被帧 2 覆盖（kDrop id 1），替代帧 2 是
  // P 对 → 序列头门丢弃（kHeadBarrier id 2）
  s.submit_road(ConnEpoch{1}, FrameIdx{1}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{1}, FrameIdx{1}, p, sizeof p, false);
  s.submit_road(ConnEpoch{1}, FrameIdx{2}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{1}, FrameIdx{2}, p, sizeof p, false);
  CHECK(evs.size() == 2 && evs[0].ev == UplinkEvent::kDrop && evs[0].idx == 1 &&
        evs[1].ev == UplinkEvent::kHeadBarrier && evs[1].idx == 2);

  s.notify_disconnect();
  CHECK(s.step());  // 重连 → 连接代号 2
  evs.clear();
  open_head(s, 2, 0, p, sizeof p, p, sizeof p);
  s.step();
  CHECK(evs.back().ev == UplinkEvent::kFrameSent && evs.back().idx == 0);
  evs.clear();

  // 新连接 frame_idx 重新编号：id 1/2 是正常新帧，不得被旧 tombstone 静默吃掉
  s.submit_road(ConnEpoch{2}, FrameIdx{1}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{2}, FrameIdx{1}, p, sizeof p, false);
  s.step();
  s.step();
  s.submit_road(ConnEpoch{2}, FrameIdx{2}, test_hdr(), p, sizeof p, false, false, false);
  s.submit_wide(ConnEpoch{2}, FrameIdx{2}, p, sizeof p, false);
  s.step();
  s.step();
  bool sent1 = false, sent2 = false;
  for (auto& e : evs) {
    if (e.ev == UplinkEvent::kFrameSent && e.idx == 1) sent1 = true;
    if (e.ev == UplinkEvent::kFrameSent && e.idx == 2) sent2 = true;
  }
  CHECK(sent1 && sent2);
}

// =====================================================================
// FrameStageLog：C4 本机分段（road 关键路径，各段首尾相接）
// =====================================================================
static void test_frame_stages_telescope() {
  FrameStageLog log;
  const uint64_t ms = 1'000'000;
  const FrameIdx k{7};
  StageMs s;
  CHECK(!log.on_reply(k, 99 * ms, &s));  // 没提交过：不报
  log.on_submit(k, 100 * ms, 120 * ms, 121 * ms, 125 * ms);  // eof / recv / warp 完 / 配对提交
  log.on_encoded(k, 128 * ms);
  log.on_encoded(k, 127 * ms);  // 两路取晚的
  CHECK(!log.on_reply(k, 160 * ms, &s));  // 还没写完：不报
  log.on_sent(k, 130 * ms);
  CHECK(log.on_reply(k, 160 * ms, &s));
  CHECK_NEAR(s.capture, 20, 1e-6);
  CHECK_NEAR(s.warp, 1, 1e-6);
  CHECK_NEAR(s.pair_wait, 4, 1e-6);
  CHECK_NEAR(s.encode, 3, 1e-6);
  CHECK_NEAR(s.send, 2, 1e-6);
  CHECK_NEAR(s.reply_wait, 30, 1e-6);
  CHECK_NEAR(s.capture + s.warp + s.pair_wait + s.encode + s.send + s.reply_wait, 60, 1e-6);
  CHECK(!log.on_reply(k, 161 * ms, &s));  // 每帧只报一次
}

static void test_frame_stages_ring_no_mismatch() {
  FrameStageLog log;
  StageMs s;
  log.on_submit(FrameIdx{1}, 1, 2, 3, 4);
  log.on_encoded(FrameIdx{1}, 5);
  log.on_sent(FrameIdx{1}, 6);
  log.on_submit(FrameIdx{1 + FrameStageLog::kSlots}, 10, 20, 30, 40);  // 同槽新帧顶掉旧帧
  CHECK(!log.on_reply(FrameIdx{1}, 7, &s));                           // 旧帧 REPLY 不许配到新帧
  log.on_encoded(FrameIdx{1}, 8);                                     // 旧帧迟到事件不污染新帧
  log.on_sent(FrameIdx{1}, 9);
  CHECK(!log.on_reply(FrameIdx{1 + FrameStageLog::kSlots}, 50, &s));  // 新帧还没编码/写完
}

// =====================================================================
// navStateSP：REPLY NavExt → 纯字段快照（main.cc 再写 capnp）
// =====================================================================

static bgm1::Reply nav_reply_fixture() {
  bgm1::Reply r;
  r.frame_idx = 4242;
  r.t_eof = 987654321ULL;
  r.nav_ext.nav_health_flags = bgm1::kNavHealthGpsOk | bgm1::kNavHealthNavOk;
  r.nav_ext.nav_state = {2, 20, 255, 2, 350, 12000, 900, 40, 0x07, 254, 9000};
  r.nav_ext.road_attr = {80, 1, 60, 3, 150, 1000, 0x85, 3, 25};
  r.nav_ext.curve_ahead.point_count = 3;
  r.nav_ext.curve_ahead.max_lateral_acceleration_dm_per_s2 = 20;
  r.nav_ext.curve_ahead.horizon_distance_m = 600;
  r.nav_ext.curve_ahead.age_ms = 120;
  r.nav_ext.curve_ahead.curve_flags = 0x05;  // 沿路线 + 已平滑
  for (int i = 0; i < 3; i++) {
    r.nav_ext.curve_ahead.points[i] = {374220000 + 100 * i, -1220840000 - 100 * i,
                                       uint16_t(50 + 10 * i), uint8_t(40 + i), uint8_t(200 + i)};
  }
  r.nav_ext.nav_debug = {4200, 1800, 35, 27};
  return r;
}

static void test_nav_state_snapshot_present_flags() {
  bgm1::Reply r = nav_reply_fixture();
  r.nav_ext.present_flags = 0;
  NavStateSnapshot s = nav_state_snapshot_from_reply(r);
  CHECK(s.frame_index == 4242 && s.timestamp_eof_ns == 987654321ULL);
  CHECK(s.gps_ok && !s.route_ok && s.nav_ok && !s.rerouting);
  CHECK(!s.has_nav_state && !s.has_road_attributes && !s.has_curve_ahead && !s.has_nav_debug);

  r.nav_ext.present_flags = bgm1::kNavPresentNavState;
  s = nav_state_snapshot_from_reply(r);
  CHECK(s.has_nav_state && !s.has_road_attributes && !s.has_curve_ahead && !s.has_nav_debug);

  r.nav_ext.present_flags = bgm1::kNavPresentRoadAttr;
  s = nav_state_snapshot_from_reply(r);
  CHECK(!s.has_nav_state && s.has_road_attributes && !s.has_curve_ahead && !s.has_nav_debug);

  r.nav_ext.present_flags = bgm1::kNavPresentCurveAhead;
  s = nav_state_snapshot_from_reply(r);
  CHECK(!s.has_nav_state && !s.has_road_attributes && s.has_curve_ahead && !s.has_nav_debug);

  r.nav_ext.present_flags = bgm1::kNavPresentNavDebug;
  s = nav_state_snapshot_from_reply(r);
  CHECK(!s.has_nav_state && !s.has_road_attributes && !s.has_curve_ahead && s.has_nav_debug);
}

static void test_nav_state_snapshot_values() {
  bgm1::Reply r = nav_reply_fixture();
  r.nav_ext.present_flags = 0x0F;
  NavStateSnapshot s = nav_state_snapshot_from_reply(r);

  CHECK(s.nav_state.status == 2 && s.nav_state.maneuver_type == 20 &&
        s.nav_state.next_maneuver_type == 255 && s.nav_state.roundabout_exit_index == 2);
  CHECK(s.nav_state.distance_to_maneuver_m == 350 && s.nav_state.route_remaining_m == 12000 &&
        s.nav_state.route_remaining_seconds == 900 && s.nav_state.age_ms == 40);
  CHECK(s.nav_state.nav_map_fed && s.nav_state.navigation_fed && s.nav_state.on_route);
  CHECK(s.nav_state.off_route_distance_m == 254);
  CHECK_NEAR(s.nav_state.heading_deg, 90.0, 1e-6);  // 9000 centideg

  CHECK(s.road_attributes.speed_limit_kph == 80 && s.road_attributes.speed_limit_source == 1 &&
        s.road_attributes.speed_limit_ahead_kph == 60 && s.road_attributes.road_class == 3 &&
        s.road_attributes.speed_limit_ahead_distance_m == 150);
  CHECK(s.road_attributes.ramp && !s.road_attributes.tunnel && s.road_attributes.bridge &&
        !s.road_attributes.roundabout && !s.road_attributes.toll && !s.road_attributes.oneway &&
        s.road_attributes.match_trusted);  // 0x85 = bit0 | bit2 | bit7
  CHECK(s.road_attributes.lane_count == 3 && s.road_attributes.age_ms == 1000);
  CHECK_NEAR(s.road_attributes.match_distance_m, 2.5, 1e-6);  // 25 dm

  CHECK(s.curve_ahead.point_count == 3 && s.curve_ahead.horizon_distance_m == 600 &&
        s.curve_ahead.age_ms == 120);
  CHECK_NEAR(s.curve_ahead.max_lateral_acceleration_m_per_s2, 2.0, 1e-6);  // 20 × 0.1
  CHECK(s.curve_ahead.points[0].distance_along_route_m == 50 &&
        s.curve_ahead.points[2].target_speed_kph == 42);
  CHECK_NEAR(s.curve_ahead.points[0].curvature_per_m, 0.0200, 1e-6);  // 200 × 1e-4
  CHECK_NEAR(s.curve_ahead.points[2].curvature_per_m, 0.0202, 1e-6);
  CHECK(s.curve_ahead.points[3].distance_along_route_m == 0);  // count 之外不镜像
  CHECK(s.curve_ahead.along_route && !s.curve_ahead.road_lookahead && s.curve_ahead.smoothed);

  CHECK(s.nav_debug.render_duration_us == 4200 && s.nav_debug.map_match_duration_us == 1800 &&
        s.nav_debug.route_compute_duration_ms == 35 && s.nav_debug.nav_cpu_usage_percent == 27);

  // 线上无效哨兵 → NaN（0 是有效值，不拿 0 表示缺失）
  r.nav_ext.nav_state.heading_centideg = bgm1::kNavU16Invalid;
  r.nav_ext.road_attr.match_distance_dm = bgm1::kNavU16Invalid;
  s = nav_state_snapshot_from_reply(r);
  CHECK(std::isnan(s.nav_state.heading_deg));
  CHECK(std::isnan(s.road_attributes.match_distance_m));
}

static void test_nav_state_snapshot_curve_flags() {
  bgm1::Reply r = nav_reply_fixture();
  r.nav_ext.present_flags = bgm1::kNavPresentCurveAhead;
  r.nav_ext.curve_ahead.curve_flags = 0x02;  // 仅道路前扫
  const NavStateSnapshot s = nav_state_snapshot_from_reply(r);
  CHECK(!s.curve_ahead.along_route && s.curve_ahead.road_lookahead && !s.curve_ahead.smoothed);
}

// 决策 17：快照只读点的距离 / 速度 / 曲率，经纬度改动不影响任何输出字段
static void test_nav_state_snapshot_ignores_curve_coordinates() {
  bgm1::Reply a = nav_reply_fixture();
  bgm1::Reply b = nav_reply_fixture();
  a.nav_ext.present_flags = b.nav_ext.present_flags = bgm1::kNavPresentCurveAhead;
  for (int i = 0; i < int(bgm1::kCurveMaxPoints); i++) {
    b.nav_ext.curve_ahead.points[i].latitude_deg_x1e7 = -a.nav_ext.curve_ahead.points[i].latitude_deg_x1e7 + 7;
    b.nav_ext.curve_ahead.points[i].longitude_deg_x1e7 = a.nav_ext.curve_ahead.points[i].longitude_deg_x1e7 + 1234567;
  }
  const NavStateSnapshot sa = nav_state_snapshot_from_reply(a);
  const NavStateSnapshot sb = nav_state_snapshot_from_reply(b);
  CHECK(sa.curve_ahead.point_count == sb.curve_ahead.point_count);
  CHECK_NEAR(sa.curve_ahead.max_lateral_acceleration_m_per_s2, sb.curve_ahead.max_lateral_acceleration_m_per_s2, 0);
  for (int i = 0; i < int(bgm1::kCurveMaxPoints); i++) {
    CHECK(sa.curve_ahead.points[i].distance_along_route_m == sb.curve_ahead.points[i].distance_along_route_m);
    CHECK(sa.curve_ahead.points[i].target_speed_kph == sb.curve_ahead.points[i].target_speed_kph);
    CHECK(sa.curve_ahead.points[i].curvature_per_m == sb.curve_ahead.points[i].curvature_per_m);
  }
}

// =====================================================================
// MapTargetWriter：CURVE_AHEAD + 本机 GNSS → MapTargetVelocities（≤2 Hz；失效/删点/降速立即写）
// =====================================================================

static GnssSample map_gnss(uint64_t now_ns, double latitude_deg = 37.422,
                           double longitude_deg = -122.084) {
  GnssSample g;
  g.has_fix = true;
  g.event_valid = true;
  g.latitude_deg = latitude_deg;
  g.longitude_deg = longitude_deg;
  g.horizontal_accuracy_m = 5.0;
  g.message_time_ns = now_ns - 100'000'000;  // 100 ms 前的 fix
  return g;
}

static MapTargetInput map_input(uint64_t now_ns = 10'000'000'000ULL) {
  MapTargetInput in;
  in.link_connected = true;
  in.curve_present = true;
  in.gnss_seen = true;
  in.gnss = map_gnss(now_ns);
  in.now_ns = now_ns;
  in.last_reply_ns = now_ns - 50'000'000;  // 50 ms 前的 REPLY
  in.t_eof = now_ns - 50'000'000;          // REPLY 回显的帧时刻（N7 的基准）
  in.curve.point_count = 8;
  in.curve.max_lateral_acceleration_dm_per_s2 = 20;
  in.curve.horizon_distance_m = 600;
  in.curve.age_ms = 100;
  for (int i = 0; i < 8; i++) {
    in.curve.points[i] = {374220000 + i, -1220840000 + i, uint16_t(10 * i),
                          uint8_t(40 + i), uint8_t(200 + i)};
  }
  return in;
}

// 真实采样点间距 ~10 m；N1 的删点判定要在大于 0.55 m 容差的距离上测
static MapTargetInput map_input_spaced(uint64_t now_ns) {
  MapTargetInput in = map_input(now_ns);
  for (int i = 0; i < 8; i++) {
    in.curve.points[i].latitude_deg_x1e7 = 374220000 + i * 1000;
    in.curve.points[i].longitude_deg_x1e7 = -1220840000 + i * 1000;
  }
  return in;
}

// AU-8：writer 输出与 tests/fixtures/map_target_eight_points.json 逐字节相等；SCC-map 的 Python 测试
// （test_map_controller.py）读同一个文件，两端格式漂移时至少一端会红。文件缺失判失败。
static std::string read_map_target_fixture() {
  const char* dir_env = std::getenv("BGM1_FIXTURE_DIR");
  std::string dir;
  if (dir_env != nullptr) {
    dir = dir_env;
  } else {
    dir = __FILE__;
    dir = dir.substr(0, dir.find_last_of('/') + 1) + "tests/fixtures";
  }
  std::FILE* f = std::fopen((dir + "/map_target_eight_points.json").c_str(), "rb");
  std::string text;
  if (f != nullptr) {
    char buf[1024];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) text.append(buf, n);
    std::fclose(f);
  }
  return text;
}

static void test_map_target_eight_points_json() {
  MapTargetWriter w;
  const auto out = w.update(map_input());
  CHECK(out.has_value());
  if (!out) return;
  // 与 SmartCruiseControlMap 单测同一格式：[{"latitude","longitude","velocity"(m/s)}…]；
  // velocity = target_speed_kph / 3.6
  const std::string expected = read_map_target_fixture();
  CHECK(!expected.empty());
  CHECK(*out == expected);
}

static void test_map_target_count_zero_is_empty_list() {
  MapTargetWriter w;
  MapTargetInput in = map_input();
  in.curve.point_count = 0;
  const auto out = w.update(in);
  CHECK(out.has_value() && *out == "[]");
}

// 失效路径都必须在 100 ms 内（不等 500 ms 节拍）发出清空（下一 calib 拍写盘）：
// present=0、断链、S4 合成过期（t_eof 基准）、无 fix、fix 过期、event_valid=0、
// 精度不达标/未知、目标速度全 0
static void expect_immediate_clear(void (*mutate)(MapTargetInput*, uint64_t)) {
  MapTargetWriter w;
  const uint64_t now = 10'000'000'000ULL;
  CHECK(w.update(map_input(now)).has_value());
  MapTargetInput in = map_input(now + 100'000'000ULL);
  mutate(&in, now + 100'000'000ULL);
  const auto out = w.update(in);
  CHECK(out.has_value() && *out == "[]");
}

static void test_map_target_invalid_paths_clear_immediately() {
  expect_immediate_clear([](MapTargetInput* in, uint64_t) { in->curve_present = false; });
  expect_immediate_clear([](MapTargetInput* in, uint64_t) { in->link_connected = false; });
  expect_immediate_clear([](MapTargetInput* in, uint64_t now) {
    in->t_eof = now - 1'990'000'000ULL;  // 帧 1.99 s 前
    in->curve.age_ms = 1999;             // 数据时刻 ≈ 3.989 s 前 → 过期（S4）
  });
  expect_immediate_clear([](MapTargetInput* in, uint64_t now) {
    in->t_eof = now - 3'000'000'000ULL;  // 帧本身超过 2 s
  });
  expect_immediate_clear([](MapTargetInput* in, uint64_t) { in->gnss.has_fix = false; });
  expect_immediate_clear([](MapTargetInput* in, uint64_t) { in->gnss.event_valid = false; });
  expect_immediate_clear([](MapTargetInput* in, uint64_t now) {
    in->gnss.message_time_ns = now - 3'000'000'000ULL;  // fix 过期
  });
  expect_immediate_clear([](MapTargetInput* in, uint64_t now) {
    in->gnss.horizontal_accuracy_m = 45.0;              // >40 m 立即清空（AU-3 迟滞；S7）
    in->gnss.message_time_ns = now;                     // 新样本
  });
  expect_immediate_clear([](MapTargetInput* in, uint64_t) {
    in->curve.point_count = 2;                          // 目标速度 0 = 线上「无」
    in->curve.points[0].target_speed_kph = 0;
    in->curve.points[1].target_speed_kph = 0;
  });

  // AU-6：数据时刻 t_eof − age_ms 恰好 2 s 前仍有效（<=），再早 1 ns 过期
  for (const bool one_ns_older : {false, true}) {
    MapTargetWriter wb;
    const uint64_t t = 10'000'000'000ULL;
    CHECK(wb.update(map_input(t)).has_value());
    MapTargetInput edge = map_input(t + 1'000'000'000ULL);
    edge.curve.age_ms = 500;
    edge.t_eof = edge.now_ns - 1'500'000'000ULL - (one_ns_older ? 1 : 0);  // 数据时刻 = now − 2 s (− 1 ns)
    edge.gnss.message_time_ns = edge.now_ns - 100'000'000ULL;
    const auto edge_out = wb.update(edge);
    if (one_ns_older) {
      CHECK(edge_out.has_value() && *edge_out == "[]");
    } else {
      CHECK(!edge_out.has_value() || *edge_out != "[]");
    }
  }

  // S4/N7 边界：
  //   ① t_eof 1.5 s 前 + age 600 ms → 数据 2.1 s 前，过期；
  //   ② 同样 age、REPLY 1 ms 前到达，也过期（证明不再用到达时刻）。
  MapTargetWriter w;
  const uint64_t now = 10'000'000'000ULL;
  CHECK(w.update(map_input(now)).has_value());
  MapTargetInput in = map_input(now + 100'000'000ULL);
  in.now_ns = now + 1'500'000'000ULL;
  in.t_eof = now;
  in.last_reply_ns = in.now_ns - 1'000'000ULL;
  in.curve.age_ms = 600;
  auto out = w.update(in);
  CHECK(out.has_value() && *out == "[]");

  // ② 用新的 writer，避免上一拍已经写过 [] 导致内容相同而不写
  MapTargetWriter w2;
  CHECK(w2.update(map_input(now)).has_value());
  MapTargetInput in2 = map_input(now + 1'500'000'000ULL);
  in2.now_ns = now + 1'500'000'000ULL;
  in2.t_eof = now;
  in2.last_reply_ns = in2.now_ns - 1'000'000ULL;  // REPLY 1 ms 前到达也不救
  in2.curve.age_ms = 600;                          // 同 age：t_eof 1.5 s 前 + 0.6 s = 2.1 s 前 → 过期
  const auto out2 = w2.update(in2);
  CHECK(out2.has_value() && *out2 == "[]");
}

static int json_point_count(const std::string& json) {
  int points = 0;
  for (size_t pos = 0; (pos = json.find("{\"latitude\"", pos)) != std::string::npos; pos++) points++;
  return points;
}

// N1：删点（即使总点数不变）也立即写
static void test_map_target_removed_point_writes_immediately() {
  // 目标点变少（没清空）
  {
    MapTargetWriter w;
    const uint64_t now = 10'000'000'000ULL;
    CHECK(w.update(map_input_spaced(now)).has_value());
    MapTargetInput in = map_input_spaced(now + 100'000'000ULL);
    in.curve.point_count = 7;
    const auto out = w.update(in);
    CHECK(out.has_value() && json_point_count(*out) == 7);
  }
  // 同数量替换首点：8 → 8，但旧点 0 不在新集合里
  {
    MapTargetWriter w;
    const uint64_t now = 10'000'000'000ULL;
    CHECK(w.update(map_input_spaced(now)).has_value());
    MapTargetInput in = map_input_spaced(now + 100'000'000ULL);
    for (int i = 0; i < 7; i++) in.curve.points[i] = in.curve.points[i + 1];
    in.curve.points[7] = {374220000 + 99, -1220840000 + 99, 99, 40, 200};
    const auto out = w.update(in);
    CHECK(out.has_value() && json_point_count(*out) == 8);
    CHECK(out && out->find("37.4220000") == std::string::npos);  // 旧首点必须被清掉
  }
  // 7 → 8：删 1 个、加 2 个
  {
    MapTargetWriter w;
    const uint64_t now = 10'000'000'000ULL;
    MapTargetInput first = map_input_spaced(now);
    first.curve.point_count = 7;
    CHECK(w.update(first).has_value());
    MapTargetInput in = map_input_spaced(now + 100'000'000ULL);
    in.curve.points[6] = {374220000 + 50, -1220840000 + 50, 60, 40, 200};
    in.curve.points[7] = {374220000 + 51, -1220840000 + 51, 70, 40, 200};
    const auto out = w.update(in);
    CHECK(out.has_value() && json_point_count(*out) == 8);
  }
}

// N2：容差内同一点降速立即写；提速仍走 500 ms
static void test_map_target_speed_change_cadence() {
  MapTargetWriter w;
  const uint64_t now = 10'000'000'000ULL;
  MapTargetInput first = map_input(now);
  for (int i = 0; i < 8; i++) first.curve.points[i].target_speed_kph = 40;
  CHECK(w.update(first).has_value());

  // 40 → 20 km/h：100 ms 内必须写
  MapTargetInput slower = map_input(now + 100'000'000ULL);
  for (int i = 0; i < 8; i++) slower.curve.points[i].target_speed_kph = 20;
  const auto out = w.update(slower);
  CHECK(out.has_value() && out->find("\"velocity\":5.556") != std::string::npos);

  // 20 → 40 km/h：仍走 500 ms
  MapTargetWriter w2;
  MapTargetInput slow_first = map_input(now);
  for (int i = 0; i < 8; i++) slow_first.curve.points[i].target_speed_kph = 20;
  CHECK(w2.update(slow_first).has_value());
  MapTargetInput faster = map_input(now + 100'000'000ULL);
  for (int i = 0; i < 8; i++) faster.curve.points[i].target_speed_kph = 40;
  CHECK(!w2.update(faster).has_value());
  faster.now_ns = now + 500'000'000ULL;
  faster.t_eof = faster.now_ns - 50'000'000ULL;
  faster.last_reply_ns = faster.now_ns - 50'000'000ULL;
  CHECK(w2.update(faster).has_value());

  // 容差匹配：点挪 0.3 m（27 × 1e-7 ≈ 0.30 m），速度也变
  // 40 → 20：100 ms 内必须写
  MapTargetWriter w3;
  MapTargetInput base = map_input_spaced(now);
  for (int i = 0; i < 8; i++) base.curve.points[i].target_speed_kph = 40;
  CHECK(w3.update(base).has_value());
  MapTargetInput moved_slower = map_input_spaced(now + 100'000'000ULL);
  moved_slower.curve.points[0].latitude_deg_x1e7 += 27;  // ≈0.30 m，在 50 容差内
  moved_slower.curve.points[0].target_speed_kph = 20;
  const auto out_slower = w3.update(moved_slower);
  CHECK(out_slower.has_value() && out_slower->find("\"velocity\":5.556") != std::string::npos);

  // 20 → 40：100 ms 内不写
  MapTargetWriter w4;
  MapTargetInput slow_base = map_input_spaced(now);
  for (int i = 0; i < 8; i++) slow_base.curve.points[i].target_speed_kph = 20;
  CHECK(w4.update(slow_base).has_value());
  MapTargetInput moved_faster = map_input_spaced(now + 100'000'000ULL);
  moved_faster.curve.points[0].latitude_deg_x1e7 += 27;
  moved_faster.curve.points[0].target_speed_kph = 40;
  CHECK(!w4.update(moved_faster).has_value());
}

// 坐标校验：纬度 200° 与 (0,0) 的点跳过（S5）
static void test_map_target_skips_bad_point_coordinates() {
  MapTargetWriter w;
  MapTargetInput in = map_input();
  in.curve.point_count = 3;
  in.curve.points[0].latitude_deg_x1e7 = 200 * 10'000'000;   // 纬度 200°
  in.curve.points[1].latitude_deg_x1e7 = 0;                  // (0,0) 视为未填
  in.curve.points[1].longitude_deg_x1e7 = 0;
  const auto out = w.update(in);
  CHECK(out.has_value());
  if (out) {
    int points = 0;
    for (size_t pos = 0; (pos = out->find("{\"latitude\"", pos)) != std::string::npos; pos++) points++;
    CHECK(points == 1);  // 只有第 3 个点
    CHECK(out->find("\"latitude\":37.4220002") != std::string::npos);
    CHECK(out->find("\"latitude\":200.") == std::string::npos);
    CHECK(out->find("\"latitude\":0.0000000") == std::string::npos);
  }
}

// R2：跨 ±180° 的 int32 相减不能溢出（-fsanitize=undefined 下验证）
static void test_map_target_antimeridian_no_int_overflow() {
  MapTargetWriter w;
  const uint64_t now = 10'000'000'000ULL;
  MapTargetInput first = map_input(now);
  first.curve.point_count = 1;
  first.curve.points[0].latitude_deg_x1e7 = 374220000;      // 纬度合法
  first.curve.points[0].longitude_deg_x1e7 = 1799999900;    // +179.99999°
  CHECK(w.update(first).has_value());

  MapTargetInput second = map_input(now + 100'000'000ULL);
  second.curve.point_count = 1;
  second.curve.points[0].latitude_deg_x1e7 = 374220000;
  second.curve.points[0].longitude_deg_x1e7 = -1799999900;  // -179.99999°
  const auto out = w.update(second);  // 旧点被删除 → 立即写（差值走 int64）
  CHECK(out.has_value() && json_point_count(*out) == 1);
}

// R4：容差内有多个候选时取最近的点；最近点降速必须判成降速
static void test_map_target_tolerance_picks_nearest() {
  MapTargetWriter w;
  const uint64_t now = 10'000'000'000ULL;
  MapTargetInput first = map_input(now);
  first.curve.point_count = 1;
  first.curve.points[0].latitude_deg_x1e7 = 374220000;
  first.curve.points[0].target_speed_kph = 40;
  CHECK(w.update(first).has_value());

  MapTargetInput next = map_input(now + 100'000'000ULL);
  next.curve.point_count = 2;
  next.curve.points[0] = {374220030, -1220840000, 10, 40, 200};  // 远 40，速度不变（先命中）
  next.curve.points[1] = {374220005, -1220840000, 10, 20, 200};  // 近 5，降速
  const auto out = w.update(next);
  CHECK(out.has_value() && out->find("\"velocity\":5.556") != std::string::npos);
}

// R5：一对一匹配；两个旧点靠得近、新集合只剩一个 → 另一个必须算删除（立即写）
static void test_map_target_one_to_one_matching() {
  MapTargetWriter w;
  const uint64_t now = 10'000'000'000ULL;
  MapTargetInput first = map_input(now);
  first.curve.point_count = 2;
  first.curve.points[0] = {374220000, -1220840000, 10, 40, 200};
  first.curve.points[1] = {374220010, -1220840000, 20, 40, 200};  // 相距 10 单位
  CHECK(w.update(first).has_value());

  MapTargetInput next = map_input(now + 100'000'000ULL);
  next.curve.point_count = 1;
  next.curve.points[0] = {374220000, -1220840000, 10, 40, 200};
  const auto out = w.update(next);
  CHECK(out.has_value() && json_point_count(*out) == 1);  // 立即写，不等 500 ms
}

// AU-1：main.cc 的 reply 线程只调 NavLinkSession，宿主测试驱动同一个类（不再手抄 main 的接线）。
static NavLinkTick link_tick(uint64_t now_ns) {
  NavLinkTick tick;
  tick.gnss.seen = true;
  tick.gnss.gnss = map_gnss(now_ns);
  tick.now_ns = now_ns;
  return tick;
}

// 带沿路线 CURVE（fixture 的 curve_flags=0x05）的 REPLY；AU-4 要求 route_ok=1 才可用
static bgm1::NavExt session_nav_ext() {
  bgm1::Reply r = nav_reply_fixture();
  r.nav_ext.present_flags = bgm1::kNavPresentCurveAhead;
  r.nav_ext.nav_health_flags |= bgm1::kNavHealthRouteOk;
  return r.nav_ext;
}

// 连上并收到一条带 3 个弯道点的 REPLY → 已写出非空目标
static void session_connect_with_curve(NavLinkSession* session, uint64_t now_ns) {
  session->on_epoch(1, [&] { return link_tick(now_ns - 1'000'000'000ULL); });  // 连接建立（首拍写 []）
  session->take_pending();
  session->on_hello_ok();
  session->on_reply(now_ns, now_ns - 50'000'000ULL, session_nav_ext(), link_tick(now_ns));
  const auto pending = session->take_pending();
  CHECK(pending.has_value() && json_point_count(*pending) == 3);
}

static void test_nav_link_session_clear_paths() {
  const uint64_t now = 10'000'000'000ULL;
  struct Case {
    const char* name;
    void (*clear)(NavLinkSession*, uint64_t);
  };
  const Case cases[] = {
      {"fail/ERR/disconnect",
       [](NavLinkSession* s, uint64_t t) { s->on_disconnect(link_tick(t)); }},
      {"epoch change",
       [](NavLinkSession* s, uint64_t t) { CHECK(s->on_epoch(2, [&] { return link_tick(t); })); }},
      {"not connected idle", [](NavLinkSession* s, uint64_t t) { s->on_idle(false, link_tick(t)); }},
  };
  for (const Case& c : cases) {
    NavLinkSession session;
    session_connect_with_curve(&session, now);
    CHECK(session.hello_seen());
    c.clear(&session, now + 100'000'000ULL);
    const auto cleared = session.take_pending();
    if (!(cleared.has_value() && *cleared == "[]")) std::printf("  case: %s\n", c.name);
    CHECK(cleared.has_value() && *cleared == "[]");
    CHECK(!session.hello_seen());
    // 重连窗口内第一条新 REPLY 前的空闲拍：连接已通，旧点不得写回
    for (const uint64_t dt : {600'000'000ULL, 1'200'000'000ULL, 1'900'000'000ULL}) {
      session.on_idle(true, link_tick(now + dt));
      const auto idle = session.take_pending();
      if (idle.has_value() && *idle != "[]") std::printf("  case: %s dt=%llu\n", c.name, (unsigned long long)dt);
      CHECK(!idle.has_value() || *idle == "[]");
    }
  }
}

static void test_nav_link_session_live_reply_keeps_targets() {
  const uint64_t now = 10'000'000'000ULL;
  NavLinkSession session;
  session_connect_with_curve(&session, now);
  // 链路正常、数据新鲜的空闲拍（无新 REPLY）：1 s 刷新仍是同样 3 点，不被清掉
  session.on_idle(true, link_tick(now + 1'000'000'000ULL));
  const auto refreshed = session.take_pending();
  CHECK(refreshed.has_value() && json_point_count(*refreshed) == 3);
  // 同一个 epoch 再次出现：不复位
  int ticks_taken = 0;
  const auto counting_tick = [&] {
    ticks_taken++;
    return link_tick(now + 1'050'000'000ULL);
  };
  CHECK(!session.on_epoch(1, counting_tick));
  CHECK(session.hello_seen());
  // AF-3：epoch 没变时不取 GNSS 快照（reply 线程每轮循环都走这里，快照要加 MetaProvider 的锁）
  CHECK(ticks_taken == 0);
  CHECK(session.on_epoch(2, counting_tick));
  CHECK(ticks_taken == 1);
}

// 旧 epoch 的 take_pending 只交出最后一次决策（覆盖语义）
static void test_nav_link_session_pending_is_last_decision() {
  const uint64_t now = 10'000'000'000ULL;
  NavLinkSession session;
  session_connect_with_curve(&session, now);
  session.on_disconnect(link_tick(now + 100'000'000ULL));
  session.on_idle(false, link_tick(now + 150'000'000ULL));
  const auto pending = session.take_pending();
  CHECK(pending.has_value() && *pending == "[]");
  CHECK(!session.take_pending().has_value());  // 取走即空
}

// NavLinkCache：断连 reset 后，即使 link_connected 又变真、旧曲线还在，也只写 []
static void test_nav_link_cache_reset_clears_old_curve() {
  NavLinkCache cache;
  MapTargetWriter w;
  bgm1::Reply r = nav_reply_fixture();
  r.nav_ext.present_flags = bgm1::kNavPresentCurveAhead;
  r.nav_ext.nav_health_flags |= bgm1::kNavHealthRouteOk;
  const uint64_t now = 10'000'000'000ULL;
  cache.on_reply(now, now - 50'000'000ULL, r.nav_ext);

  auto input_from_cache = [&](uint64_t t, bool link) {
    MapTargetInput in = map_input(t);
    in.link_connected = link;
    in.curve_present = cache.present_flags() & bgm1::kNavPresentCurveAhead;
    in.curve = cache.curve();
    in.nav_health_flags = cache.nav_health_flags();
    in.last_reply_ns = cache.last_reply_ns();
    in.t_eof = cache.t_eof();
    return in;
  };
  const auto first = w.update(input_from_cache(now, true));
  CHECK(first.has_value() && json_point_count(*first) == 3);
  CHECK(cache.nav_health_flags() == r.nav_ext.nav_health_flags);

  // fail()：cache.reset() + link=false 立即写 []
  CHECK(cache.present_flags() == bgm1::kNavPresentCurveAhead && cache.curve().point_count > 0);
  cache.reset();
  // AU-7：reset 必须真的清掉各字段（不能靠 t_eof=0 一个字段挡住新鲜度而掩盖其他字段残留）
  CHECK(cache.present_flags() == 0 && cache.curve().point_count == 0 && cache.t_eof() == 0 &&
        cache.last_reply_ns() == 0 && cache.nav_health_flags() == 0);
  const auto cleared = w.update(input_from_cache(now, false));
  CHECK(cleared.has_value() && *cleared == "[]");

  // 600 ms 后重连、第一条新 REPLY 还没到：即使 link=true，也**绝不能**把旧点写回
  // （修复前：old cache 仍在 2 s 内 → 这里会写回 3 个旧点）
  const auto out = w.update(input_from_cache(now + 600'000'000ULL, true));
  CHECK(!out.has_value() || *out == "[]");
}

// AU-3：定位精度迟滞。不可用→可用要 ≤30 m；可用→不可用要 >40 m，或连续 3 条 >30 m；未知视同不达标。
// 计数按 GNSS 样本（message_time_ns 变化）推进，20 Hz 拍重复看到同一条样本不累加。
static GnssSample gate_sample(uint64_t message_ns, double accuracy_m) {
  GnssSample g = map_gnss(message_ns + 100'000'000ULL);
  g.message_time_ns = message_ns;
  g.horizontal_accuracy_m = accuracy_m;
  return g;
}

static void test_gps_accuracy_gate_hysteresis() {
  uint64_t t = 10'000'000'000ULL;
  auto feed = [&](GpsAccuracyGate* gate, double accuracy_m, int ticks = 2) {
    // 一条 GNSS 样本被 reply 线程的 `ticks` 个拍看到
    const GnssSample g = gate_sample(t, accuracy_m);
    bool usable = false;
    for (int i = 0; i < ticks; i++) usable = gate->usable(g, t + 10'000'000ULL + i * 50'000'000ULL);
    t += 100'000'000ULL;
    return usable;
  };

  {  // 29/31 m 交替：一直可用
    GpsAccuracyGate gate;
    CHECK(feed(&gate, 29.0));
    for (int i = 0; i < 40; i++) CHECK(feed(&gate, i % 2 ? 29.0 : 31.0));
  }
  {  // 连续 3 条 31 m 清空；2 条不清；每条被看到 2 次也只算 1 条
    GpsAccuracyGate gate;
    CHECK(feed(&gate, 5.0));
    CHECK(feed(&gate, 31.0));
    CHECK(feed(&gate, 31.0));
    CHECK(!feed(&gate, 31.0));
    // 清空后 31 m 仍不可用；要回到 ≤30 m 才恢复
    CHECK(!feed(&gate, 31.0));
    CHECK(!feed(&gate, 35.0));
    CHECK(feed(&gate, 30.0));  // 30 m 边界可用
  }
  {  // 中间夹一条 ≤30 m：连续计数清零
    GpsAccuracyGate gate;
    CHECK(feed(&gate, 5.0));
    CHECK(feed(&gate, 31.0));
    CHECK(feed(&gate, 31.0));
    CHECK(feed(&gate, 30.0));
    CHECK(feed(&gate, 31.0));
    CHECK(feed(&gate, 31.0));
    CHECK(!feed(&gate, 31.0));
  }
  {  // 一条 >40 m 立即清空（40 m 本身不算）
    GpsAccuracyGate gate;
    CHECK(feed(&gate, 5.0));
    CHECK(feed(&gate, 40.0));
    CHECK(!feed(&gate, 40.001, 1));
    GpsAccuracyGate gate2;
    CHECK(feed(&gate2, 5.0));
    CHECK(!feed(&gate2, 45.0, 1));
  }
  {  // 未知精度：从不可用恢复不了；可用时按「不达标」计入连续 3 条，但不立即清空
    GpsAccuracyGate gate;
    CHECK(!feed(&gate, 0.0));
    CHECK(!feed(&gate, NAN));
    CHECK(!feed(&gate, -1.0));
    CHECK(feed(&gate, 5.0));
    CHECK(feed(&gate, 0.0));
    CHECK(feed(&gate, NAN));
    CHECK(!feed(&gate, 0.0));
  }
  {  // 起始 31 m 不可用；fix 丢失 / event_valid=0 / 过期立即不可用，并复位迟滞状态
    GpsAccuracyGate gate;
    CHECK(!feed(&gate, 31.0));
    CHECK(feed(&gate, 29.0));
    GnssSample no_fix = gate_sample(t, 5.0);
    no_fix.has_fix = false;
    CHECK(!gate.usable(no_fix, t + 10'000'000ULL));
    t += 100'000'000ULL;
    CHECK(!feed(&gate, 31.0));  // 复位后 31 m 不能直接恢复
    CHECK(feed(&gate, 29.0));
    // 样本过期（>1 s 没新样本）
    const GnssSample g = gate_sample(t, 5.0);
    CHECK(gate.usable(g, t + 500'000'000ULL));
    CHECK(!gate.usable(g, t + 1'100'000'000ULL));
  }
}

// AU-3 端到端：29/31 m 交替（每条 GNSS 换一次），目标不再被反复清空写回
static void test_map_target_accuracy_flapping_keeps_targets() {
  for (const int period : {1, 2, 5, 10}) {
    MapTargetWriter w;
    const uint64_t t0 = 10'000'000'000ULL;
    int clears = 0;
    int present_ticks = 0;
    bool have_targets = false;
    for (int k = 0; k < 200; k++) {
      const uint64_t now = t0 + uint64_t(k) * 50'000'000ULL;
      MapTargetInput in = map_input_spaced(now);
      in.curve.point_count = 1;
      const double accuracy_m = ((k / 2 / period) % 2) ? 31.0 : 29.0;
      in.gnss = gate_sample(t0 + uint64_t(k / 2) * 100'000'000ULL, accuracy_m);
      if (const auto out = w.update(in)) {
        if (*out == "[]") clears++;
        have_targets = *out != "[]";
      }
      if (have_targets) present_ticks++;
    }
    // period=10：每 1 s 在 31 m 上停留 10 条（≥3 条）→ 合理清空；period ≤2：只有 1–2 条，不得清空
    if (period <= 2) {
      CHECK(clears == 0);
      CHECK(present_ticks >= 199);
    } else {
      CHECK(clears >= 1);
    }
  }
}

// AU-4：偏航重算 / 已离开路线时，车机不用 CURVE 点
static void test_map_target_rerouting_and_route_ok() {
  expect_immediate_clear([](MapTargetInput* in, uint64_t) {
    in->nav_health_flags = bgm1::kNavHealthRerouting | bgm1::kNavHealthRouteOk;
  });
  expect_immediate_clear([](MapTargetInput* in, uint64_t) {
    in->curve.curve_flags = 0x01;  // 沿路线
    in->nav_health_flags = 0;      // route_ok=0
  });
  expect_immediate_clear([](MapTargetInput* in, uint64_t) {
    in->curve.curve_flags = 0x05;  // 沿路线 + 平滑
    in->nav_health_flags = bgm1::kNavHealthGpsOk;
  });

  // 沿路线 + route_ok=1：照常写
  {
    MapTargetWriter w;
    MapTargetInput in = map_input();
    in.curve.curve_flags = 0x01;
    in.nav_health_flags = bgm1::kNavHealthRouteOk;
    const auto out = w.update(in);
    CHECK(out.has_value() && json_point_count(*out) == 8);
  }
  // 沿当前道路前扫（bit1）且 route_ok=0：没有路线也可用，照常写
  {
    MapTargetWriter w;
    MapTargetInput in = map_input();
    in.curve.curve_flags = 0x02;
    in.nav_health_flags = 0;
    const auto out = w.update(in);
    CHECK(out.has_value() && json_point_count(*out) == 8);
  }
}

// S3/S7：位置按 GNSS 节拍生成；无 fix / fix 过期 / 精度未知或 >30 m / 坐标非法都不写
static void test_last_gps_position_gate() {
  const uint64_t now = 10'000'000'000ULL;
  GnssSample g = map_gnss(now);
  const auto ok = last_gps_position_json(g, now);
  CHECK(ok.has_value() && *ok == "{\"latitude\":37.4220000,\"longitude\":-122.0840000}");

  g.horizontal_accuracy_m = 30.0;  // 边界：30 m 可用
  CHECK(last_gps_position_json(g, now).has_value());
  g.horizontal_accuracy_m = 30.001;
  CHECK(!last_gps_position_json(g, now).has_value());
  g.horizontal_accuracy_m = 0.0;  // 未知哨兵
  CHECK(!last_gps_position_json(g, now).has_value());
  g.horizontal_accuracy_m = -1.0;
  CHECK(!last_gps_position_json(g, now).has_value());
  g.horizontal_accuracy_m = NAN;
  CHECK(!last_gps_position_json(g, now).has_value());

  g = map_gnss(now);
  g.has_fix = false;
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.event_valid = false;  // N6
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.message_time_ns = now - 950'000'000ULL;  // N3：0.95 s 仍有效
  CHECK(last_gps_position_json(g, now).has_value());
  g.message_time_ns = now - 1'050'000'000ULL;  // N3：1.05 s 过期
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.message_time_ns = now - 1'000'000'000ULL;  // AU-6：恰好 1 s 可用（within 是 <=）
  CHECK(last_gps_position_json(g, now).has_value());
  g.message_time_ns = now - 1'000'000'001ULL;  // AU-6：再早 1 ns 过期
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.message_time_ns = now + 10'000'000ULL;  // R3：fix 比 now 晚 10 ms（未来）按无效处理
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.message_time_ns = now - 2'000'000'001ULL;
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.latitude_deg = 91.0;
  CHECK(!last_gps_position_json(g, now).has_value());
  g = map_gnss(now);
  g.latitude_deg = 0.0;
  g.longitude_deg = 0.0;
  CHECK(!last_gps_position_json(g, now).has_value());
}

// P6 主场景：20 Hz × 10 s，车在动（位置每 50 ms 变化），GNSS 10 Hz 刷新。
// 位置写 ≈100 次（10 Hz），弯道目标 ≤21 次（首次 + ≤2 Hz）。
static void test_map_target_and_position_cadence() {
  MapTargetWriter w;
  uint64_t now = 1'000'000'000ULL;
  int position_writes = 0;
  int target_writes = 0;
  for (int tick = 0; tick < 200; tick++, now += 50'000'000ULL) {
    GnssSample g = map_gnss(now, 37.422 + tick * 1e-7, -122.084);  // 车在动
    g.message_time_ns = now;  // 只在 GNSS 更新拍（10 Hz）调用
    if (tick % 2 == 0 && last_gps_position_json(g, now)) position_writes++;

    MapTargetInput in = map_input(now);
    in.gnss = g;
    in.gnss.message_time_ns = now - 100'000'000;
    in.last_reply_ns = now - 50'000'000;
    if (w.update(in)) target_writes++;
  }
  CHECK(position_writes == 100);  // 10 s × 10 Hz
  CHECK(target_writes >= 1 && target_writes <= 21);  // 首次 + ≤2 Hz（10 s）
}

// ConnEpochTracker：drop 后 writer 线程已重连而 reply 线程还没看到断连的窗口
static void test_conn_epoch_tracker() {
  ConnEpochTracker t;
  CHECK(t.update(5));   // 首次：启动复位
  CHECK(!t.update(5));
  CHECK(t.update(6));   // writer 线程重连，reply 线程还没看到 !connected
  CHECK(!t.update(6));
}

// N10：点坐标每拍都变、点数不变，20 Hz × 10 s → 目标写 ≤21 次。
// 位置节拍由 main.cc 的 gpsLocationExternal 回调保证（宿主跑不了 calib_thread），需上车验证。
static void test_map_target_moving_points_cadence() {
  MapTargetWriter w;
  uint64_t now = 1'000'000'000ULL;
  int writes = 0;
  for (int tick = 0; tick < 200; tick++, now += 50'000'000ULL) {
    MapTargetInput in = map_input_spaced(now);
    for (int i = 0; i < 8; i++) in.curve.points[i].latitude_deg_x1e7 += tick;
    if (w.update(in)) writes++;
  }
  CHECK(writes >= 1 && writes <= 21);  // 首次 + ≤2 Hz；10 s 窗口
}

static void test_map_target_reply_rate_cpu() {
  // 20 Hz × 10 s（200 次）内容不变的 update：测量窗口 10 s 的模拟节拍、墙钟计时仅为 CPU 时间
  MapTargetWriter w;
  uint64_t now = 1'000'000'000ULL;
  CHECK(w.update(map_input(now)).has_value());
  const auto t0 = std::chrono::steady_clock::now();
  for (int i = 0; i < 200; i++) {
    now += 50'000'000ULL;
    w.update(map_input(now));
  }
  const double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count();
  printf("MapTargetWriter: 200 calls in %.1f us (%.2f us/call)\n", us, us / 200.0);
}

// S1a：退出清理必须写 MapTargetVelocities=[] 并删 LastGPSPosition
static void test_clear_map_targets_ops() {
  std::vector<std::pair<std::string, std::string>> puts;
  std::vector<std::string> removes;
  clear_map_targets(MapParamsOps{
      [&](const char* key, const std::string& value) { puts.emplace_back(key, value); },
      [&](const char* key) { removes.emplace_back(key); }});
  CHECK(puts.size() == 1 && puts[0].first == "MapTargetVelocities" && puts[0].second == "[]");
  CHECK(removes.size() == 1 && removes[0] == "LastGPSPosition");
}

// =====================================================================
// 下行分帧：REPLY 8480 B 分两次到达（切点在 NavExt 中间）→ 拼成整条
// =====================================================================

static void test_msg_framer_reply_split_inside_nav_ext() {
  bgm1::Reply r;
  r.frame_idx = 7;
  r.t_eof = 123456789ULL;
  r.nav_ext.present_flags = bgm1::kNavPresentCurveAhead;
  r.nav_ext.nav_health_flags = bgm1::kNavHealthNavOk;
  r.nav_ext.curve_ahead.point_count = 8;
  for (int i = 0; i < 8; i++) {
    r.nav_ext.curve_ahead.points[i] = {374220000 + i, -1220840000 - i, uint16_t(10 * i),
                                       uint8_t(40 + i), uint8_t(200 + i)};
  }
  std::vector<uint8_t> wire(bgm1::kReplyWireSize);
  CHECK(bgm1::pack_reply(r, wire.data(), wire.size()) == wire.size());

  // 切点：NavExt 内、CURVE_AHEAD 第 2 个点中间（REPLY 8304 + 44 + 8 + 12 + 6）
  const size_t cut = 8304 + 44 + 8 + 12 + 6;
  CHECK(cut > bgm1::kReplyHdrSize && cut < wire.size());

  MsgFramer f;
  MsgFramer::Message m;
  f.append(wire.data(), cut);
  CHECK(f.next(&m) == MsgFramer::Result::kNeedMore);
  // 再少 1 字节仍须等（挡住「差一字节也交出整条」的 off-by-one）
  f.append(wire.data() + cut, wire.size() - cut - 1);
  CHECK(f.next(&m) == MsgFramer::Result::kNeedMore);
  f.append(wire.data() + wire.size() - 1, 1);
  CHECK(f.next(&m) == MsgFramer::Result::kMessage);
  CHECK(m.type == bgm1::kTypeReply && m.size == bgm1::kReplyWireSize);
  bgm1::Reply out;
  CHECK(bgm1::parse_reply(f.data(), f.size(), &out) == bgm1::Err::kOk);
  CHECK(out.frame_idx == 7 && out.nav_ext.curve_ahead.point_count == 8 &&
        out.nav_ext.curve_ahead.points[7].curvature_per_m_x1e4 == 207);
  f.consume(m.size);
  CHECK(f.next(&m) == MsgFramer::Result::kNeedMore);

  // 同一批字节里两条消息（REPLY + ERR）按序各取一条
  bgm1::ErrMsg e;
  e.code = bgm1::kErrBadFrame;
  e.frame_idx = 7;
  std::vector<uint8_t> two(wire.size() + bgm1::kErrWireSize);
  std::memcpy(two.data(), wire.data(), wire.size());
  CHECK(bgm1::pack_err(e, two.data() + wire.size(), bgm1::kErrWireSize) == bgm1::kErrWireSize);
  MsgFramer g;
  g.append(two.data(), two.size());
  CHECK(g.next(&m) == MsgFramer::Result::kMessage && m.type == bgm1::kTypeReply);
  g.consume(m.size);
  CHECK(g.next(&m) == MsgFramer::Result::kMessage && m.type == bgm1::kTypeErr &&
        m.size == bgm1::kErrWireSize);
  g.consume(m.size);
  CHECK(g.next(&m) == MsgFramer::Result::kNeedMore);
}

// 旧 v2 REPLY（len 8280）在分帧处就判协议头非法（到不了 parse_reply）
static void test_msg_framer_rejects_v2_reply_header() {
  uint8_t hdr[bgm1::kMsgHdrSize] = {};
  hdr[0] = 'B'; hdr[1] = 'G'; hdr[2] = 'M'; hdr[3] = '1';
  hdr[4] = 2;
  hdr[5] = bgm1::kTypeReply;
  hdr[12] = 0x88; hdr[13] = 0x20;  // 8280
  MsgFramer f;
  f.append(hdr, sizeof hdr);
  MsgFramer::Message m;
  CHECK(f.next(&m) == MsgFramer::Result::kBadHeader);
  CHECK(f.peer_version() == 2);
}

int main() {
  test_nav_state_snapshot_present_flags();
  test_nav_state_snapshot_values();
  test_nav_state_snapshot_curve_flags();
  test_nav_state_snapshot_ignores_curve_coordinates();
  test_map_target_eight_points_json();
  test_map_target_count_zero_is_empty_list();
  test_map_target_invalid_paths_clear_immediately();
  test_map_target_removed_point_writes_immediately();
  test_map_target_speed_change_cadence();
  test_map_target_skips_bad_point_coordinates();
  test_map_target_antimeridian_no_int_overflow();
  test_map_target_tolerance_picks_nearest();
  test_map_target_one_to_one_matching();
  test_nav_link_cache_reset_clears_old_curve();
  test_conn_epoch_tracker();
  test_gps_accuracy_gate_hysteresis();
  test_map_target_accuracy_flapping_keeps_targets();
  test_map_target_rerouting_and_route_ok();
  test_nav_link_session_clear_paths();
  test_nav_link_session_live_reply_keeps_targets();
  test_nav_link_session_pending_is_last_decision();
  test_last_gps_position_gate();
  test_map_target_and_position_cadence();
  test_map_target_moving_points_cadence();
  test_map_target_reply_rate_cpu();
  test_clear_map_targets_ops();
  test_msg_framer_reply_split_inside_nav_ext();
  test_msg_framer_rejects_v2_reply_header();
  test_frame_stages_telescope();
  test_frame_stages_ring_no_mismatch();
  test_sched_gop();
  test_sched_drop();
  test_sched_drop_dedup();
  test_sched_stream_gap_no_dedup();
  test_sched_submit_time_slot_hole();
  test_frame_indexer();

  test_sender_road_out_goes();
  test_sender_flags();
  test_sender_queue_overwrite();
  test_sender_stall_finish();
  test_sender_stall_truncate();
  test_sender_reconnect_linklost();
  test_sender_submit_while_down();
  test_sender_partial_writes();
  test_sender_write_error();
  test_sender_wait_for_work_wakes_on_submit();
  test_sender_wait_idle_sleeps_full_cap();
  test_sender_wait_disconnected_keeps_retry_gap();
  test_sender_wait_socket_blocked_keeps_polling();
  test_sender_wait_wide_until_stall_deadline();
  test_sender_notify_disconnect_wakes_writer();
  test_link_state();
  test_avahi_parse();
  test_avahi_scope();
  test_avahi_locator();

  test_frame_codec_roundtrip();
  test_reply_err_roundtrip();
  test_v3_normalisation();
  test_cross_end_golden();

  test_warp_golden();
  test_meta_provider();
  test_meta_provider_model_inputs();
  test_meta_gnss_fix();
  test_meta_gnss_no_fix_and_age();
  test_meta_gnss_saturation();
  test_meta_vehicle_device_motion();
  test_device_motion_transform();
  test_yaw_sign_convention();
  test_device_motion_validity_flags();
  test_meta_vehicle_phase();
  test_meta_fill_zero_alloc();

  test_pair_kill_does_not_recover();
  test_pair_off_fid_aligned_sof();
  test_pair_same_fid_offset_sof();
  test_pair_tolerance_boundary();
  test_pair_replace_and_evict();
  test_pair_rate_mismatch_sim();

  test_meta_cache_clear_then_stale_take();
  test_meta_cache_stale_classification();
  test_meta_cache_dead_prefix_and_gap();
  test_head_barrier_p_pair_dropped();
  test_head_barrier_after_stream_gap();
  test_sender_conn_epoch_rejects_stale();
  test_sender_dropped_window_not_cross_connection();

  printf("%d checks, %d fails\n", g_checks, g_fails);
  return g_fails == 0 ? 0 : 1;
}
