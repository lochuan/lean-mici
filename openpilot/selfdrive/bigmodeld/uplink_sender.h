#pragma once

// 发送/重连状态机（16 号 (c)）+ REPLY 接收记账（(e)）。纯逻辑 + 可注入 socket/时钟，
// 宿主单测用假 socket/假时钟（test_bigmodeld.cc）。
//
// 口径（README「实现口径」）：
//   - TCP NODELAY；road 出包即发：submit_road 后 chunk1（头 160 B ‖ road 段）即可发，
//     chunk2（wide_len ‖ wide ‖ MAC 16 B）等 wide 包到齐再发（10 号布局分块发送，
//     与 fake_c4_client.py 同款）。
//   - 每路一个在途槽 + 一个排队槽：在途帧（正在发送）发完不撕裂；排队槽只留最新，
//     新包覆盖排队槽 = 丢弃旧包（kDrop 上报「丢帧」）；旧 frame_idx 的迟到包静默丢弃
//     （丢帧已随覆盖上报）。
//   - 假死 = 对在途帧 ≥200 ms 无任何进展（socket 写阻塞，或 wide 段迟迟不到）：
//     尝试发完在途帧（写得动就发完，不撕裂），否则截断，随后重连，上报 kStall。
//   - 连接断开/发送失败重连：每次 connect 1 s 超时；连续 3 次失败升级 kLinkLost
//     （「连接丢失」），此后持续重试、成功即复位；建连/重连成功报 kNewConnection
//     （调用方重置 frame_idx 与 I 帧状态）。重连清空在途/排队（旧帧不带进新连接）。
//
// 线程模型：submit_road/submit_wide 由编码回调线程调，step() 由发送线程调，
// notify_disconnect() 由 REPLY 接收线程调；内部互斥。事件回调在锁内触发
// （调用方不得回调进本类）。

#include <cstdint>
#include <functional>
#include <mutex>
#include <vector>

#include "frame_codec.h"

// 可注入系统接缝（宿主测试用假 socket）
class UplinkSocket {
 public:
  virtual ~UplinkSocket() = default;
  virtual bool connect(int timeout_ms) = 0;                            // 建连；超时/失败 false
  virtual int write_some(const uint8_t* data, size_t len) = 0;         // >0 已写 / 0 会阻塞 / -1 错误
  virtual int read_some(uint8_t* data, size_t len) = 0;                // >0 已读 / 0 无数据 / -1 错误或 EOF
  virtual void close() = 0;
};

enum class UplinkEvent {
  kNewConnection,  // 连接建立/重连成功：调用方重置 frame_idx 与 I 帧状态（scheduler.on_connect）
  kStall,          // 假死（≥200 ms 无进展）：发完在途帧或截断后重连，上报重置
  kLinkLost,       // 连续 3 次重连失败（每次 1 s 超时）升级「连接丢失」
  kDrop,           // 排队旧帧被覆盖丢弃（丢帧）
  kFrameSent,      // 一帧完整发出
};

struct UplinkEventInfo {
  UplinkEvent ev;
  uint32_t frame_idx;
};

struct UplinkSenderConfig {
  uint64_t stall_ms = 200;         // 假死阈值
  int connect_timeout_ms = 1000;   // 每次重连超时
  int max_connect_failures = 3;    // 连续失败升级「连接丢失」
};

class UplinkSender {
 public:
  using EventFn = std::function<void(const UplinkEventInfo&)>;

  UplinkSender(UplinkSocket* sock, std::function<uint64_t()> now_ms,
               UplinkSenderConfig cfg, EventFn on_event);

  // road 包出包即调（编码回调）。hdr 填好 t_eof/desire/traffic_convention/action_t/warp_*
  //（MetaProvider::fill）；flags 由本函数按 road_idr_actual（bit0）与 wide_idr_predicted
  //（bit1）置位——bit0 用实际 keyframe 位（精确）、bit1 用 I 帧策略预测（头先于 wide 包发出）。
  void submit_road(uint32_t frame_idx, const bgm1::FrameHeader& hdr,
                   const uint8_t* road, size_t road_len,
                   bool road_idr_actual, bool wide_idr_predicted);
  // wide 包到达时调（编码回调）。wide_actual_idr 与预测不符会记入 wide_idr_mismatches()。
  void submit_wide(uint32_t frame_idx, const uint8_t* wide, size_t wide_len, bool wide_actual_idr);

  // 发送循环单步（发送线程）；返回本次是否有进展（无进展时调用方可以小睡）。
  bool step();

  // 读侧发现断连/EOF/ERR（REPLY 接收线程）→ 走重连路径。
  void notify_disconnect();

  // ---- 状态查询（统计/测试）----
  bool connected() const;
  int connect_failures() const;
  uint64_t wide_idr_mismatches() const;  // bit1 预测与 wide 包实际 keyframe 不符次数

 private:
  struct OutFrame {
    uint32_t frame_idx = 0;
    bool has_road = false;
    bool has_wide = false;
    bool wide_idr_predicted = false;
    std::vector<uint8_t> chunk1;  // 头 160 B ‖ road 段
    std::vector<uint8_t> chunk2;  // wide_len u32 ‖ wide 段 ‖ MAC 16 B（零）
    size_t sent1 = 0;
    size_t sent2 = 0;
    bool chunk1_done = false;
  };

  OutFrame* find_or_create_locked(uint32_t frame_idx);
  void drop_connection_locked();
  void try_finish_inflight_locked();

  // 在途帧写出推进（连续部分写直到整帧发完/写不动/写错误——部分写不截断）。
  // track_progress 为假时不更新进展时戳（假死补发路径）。
  enum class WriteState { kDone, kBlocked, kError };
  WriteState write_out_locked(OutFrame& f, uint64_t now, bool track_progress);

  void emit_locked(UplinkEvent ev, uint32_t frame_idx);

  UplinkSocket* sock_;
  std::function<uint64_t()> now_;
  UplinkSenderConfig cfg_;
  EventFn on_event_;

  mutable std::mutex mtx_;
  bool connected_ = false;
  int connect_failures_ = 0;
  bool has_inflight_ = false;
  OutFrame inflight_;
  bool has_queued_ = false;
  OutFrame queued_;
  uint64_t last_progress_ms_ = 0;
  uint64_t wide_idr_mismatches_ = 0;
};

// ---- REPLY 接收（(e)）：独立接收线程收下解析、缓存最新、分段遥测统计 ----
// TODO(04 号)：outputs[0:2066) 与遥测经 msgq 转交 modeld（本类只留缓存 + 统计）。
class ReplyTracker {
 public:
  // 遥测四段（09 号）：srv_recv_us / srv_prep_us / srv_htp_us / srv_total_us
  struct SegmentStats {
    uint64_t count = 0;
    double sum_us = 0;
    uint32_t min_us = 0xffffffffu;
    uint32_t max_us = 0;
    double mean_us() const { return count ? sum_us / (double)count : 0.0; }
    void add(uint32_t v) {
      count++;
      sum_us += v;
      if (v < min_us) min_us = v;
      if (v > max_us) max_us = v;
    }
  };
  static constexpr int kSegments = 4;
  static const char* segment_name(int i);  // recv/prep/htp/total

  void on_reply(const bgm1::Reply& r);
  void on_err(const bgm1::ErrMsg& e);
  void on_disconnect();

  const bgm1::Reply& latest() const { return latest_; }
  bool has_reply() const { return has_reply_; }
  const SegmentStats& segment(int i) const { return segments_[i]; }
  uint64_t replies() const { return replies_; }
  uint64_t seq_reset() const { return seq_reset_; }  // REPLY flags bit0：服务端新建序列
  uint64_t zero_pair() const { return zero_pair_; }  // REPLY flags bit1：t-4 用了零图
  bool has_err() const { return has_err_; }
  const bgm1::ErrMsg& last_err() const { return last_err_; }

 private:
  bgm1::Reply latest_ = {};
  bool has_reply_ = false;
  SegmentStats segments_[kSegments] = {};
  uint64_t replies_ = 0;
  uint64_t seq_reset_ = 0;
  uint64_t zero_pair_ = 0;
  bgm1::ErrMsg last_err_ = {};
  bool has_err_ = false;
};
