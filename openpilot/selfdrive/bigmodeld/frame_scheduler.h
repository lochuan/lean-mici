#pragma once

// I 帧/丢帧状态机（16 号 (b)）：纯逻辑事件输出，调用方执行 request_keyframe()。
//
// 口径（README「实现口径」）：
//   - 两路基准 GOP 20（编码器配置 NUM_P_FRAMES=19），wide 相位比 road 晚 10 帧
//     （road IDR = seq 0,20,40,…；wide IDR = seq 0,10,30,50,…，与 12 号素材同分布）。
//   - 每条连接起始、每次丢帧后：两路 request_keyframe() —— request 必须先于下一个
//     提交帧的 encode_frame（21 号实测「下一帧立即 I 帧」），故事件在提交时刻之前发出，
//     落点 = 事件后的第一个提交帧 = 新序列第 0 帧。
//   - 新序列第 10 帧给 wide 再补一次 request_keyframe()（恢复「wide 晚 10 帧」相位）。
//   - 丢帧 = 实际发出 frame_idx 序列出现空洞（on_frame_sent 兜底检测）；配对缺帧
//     （on_frame_dropped）与发送队列显式丢弃（on_frame_dropped）即时上报，两者互斥：
//     显式上报后 have_last_sent_=false，空洞不再重复触发。
//   - IDR 预测（FRAME 头 flags）：bit0 = road 段 IDR（调用方再与实际 keyframe 位与）、
//     bit1 = wide 段 IDR（预测值；服务端 hevc_decoder 以 NAL 实读为准，flags 只做
//     「置位必须为真」的一致性检查，故预测只许欠报不许误报——request 落点即保证）。
//
// frame_idx 语义（用户拍板 2026-09-28）：frame_idx = VisionIpcBufExtra.frame_id − 基准，
// 每条连接从 0 起（基准由调用方在 kNewConnection 事件后重置）。相机缺帧/未配对/队列
// 丢弃全都表现为序号断档 = CONTEXT.md「丢帧」。

#include <cstdint>

struct SchedStep {
  bool request_keyframe_road = false;
  bool request_keyframe_wide = false;
  bool drop_detected = false;  // 本次输入触发丢帧语义（新序列开始）
  bool road_idr = false;       // 仅 on_frame_submit 填：本帧 IDR 预测（FRAME 头 flags）
  bool wide_idr = false;

  bool any() const {
    return request_keyframe_road || request_keyframe_wide || drop_detected || road_idr || wide_idr;
  }
};

class FrameScheduler {
 public:
  // 连接建立/重连成功（kNewConnection）：新序列，两路 request（落在下一个提交帧）。
  SchedStep on_connect();

  // 帧提交编码前调用（事件必须先于本帧 encode_frame）。seq 位置 10 给 wide 补 I，
  // 返回本帧 IDR 预测；提交序缺口在此兜底（未显式上报的死亡帧）。
  SchedStep on_frame_submit(uint32_t frame_idx);

  // 帧在进编码器前死亡（配对缺帧）或被发送队列显式丢弃：立即新序列。
  SchedStep on_frame_dropped(uint32_t frame_idx);

  // 实际发出一帧：实际发出 frame_idx 序列出现空洞 = 丢帧（显式上报之外的兜底）。
  SchedStep on_frame_sent(uint32_t frame_idx);

 private:
  SchedStep start_new_sequence(bool drop_detected);

  int seq_pos_ = 0;
  bool pending_req_road_ = false;
  bool pending_req_wide_ = false;
  int next_idr_seq_road_ = 0;
  int next_idr_seq_wide_ = 0;

  bool have_last_submit_ = false;
  uint32_t last_submit_idx_ = 0;
  bool have_last_sent_ = false;
  uint32_t last_sent_idx_ = 0;
};
