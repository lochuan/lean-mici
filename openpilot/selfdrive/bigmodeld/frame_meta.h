#pragma once

// 帧头元数据（16 号 (d)）：
//   t_eof             = road 帧 timestamp_eof（ns，调用方给）
//   warp_road/warp_wide = C++ 复刻 openpilot/common/transformations/model.py:65-70
//                       get_warp_matrix（输入 extrinsicsCalibration.rpyCalib + 相机内参常量），
//                       与 lean modeld 送进 frame_prepare 的 3×3 矩阵同义（行主序 f32，
//                       modeld.py:330,333 的 .astype(np.float32)）；
//                       宿主 golden 对照 Python ≤1e-5（gen_warp_golden.py → test_bigmodeld.cc）
//   traffic_convention = [1, 0]（lean modeld is_rhd=False 硬编码，modeld.py:321,336）
//   desire[8] = modeld DesireHelper 电平（set_model_inputs 进）→ fill 转 pulse 边沿
//     （MODEL_ABI §4.2：通道 0 恒 0、持续同 desire 只 pulse 一次）。
//   action_t[2] = modeld chestnut 公式直通（13 号 / research/02 §4，权威在 modeld）。
//   GNSS 16 B / VEHICLE 8 B = 协议 v3 §1.2.1/§1.2.2：set_gnss/set_vehicle 存原始单位，
//     fill 按 t_eof 算 age_ms / has_fix / yaw_valid 并编码单位；线上规范形（无 fix 清位置类
//     字段、valid=0 写 0、bearing_accuracy_half_deg 无 fix 255）归 frame_codec 的 pack。
//
// 纯逻辑（仅 std），宿主单测直接编译；fill 路径不堆分配。

#include <cstdint>
#include <mutex>

#include "frame_codec.h"

// C++ 复刻 get_warp_matrix（common/transformations/model.py:65-70）：
//   warp = K_cam @ view_frame_from_device_frame @ rot_from_euler(rpy) @ inv(K_model @ view_frame_from_device_frame)
//   view_frame_from_device_frame = device_frame_from_view_frame.T（camera.py:65-66）
//   bigmodel_frame=false → K_model = medmodel_intrinsics（road → MED）
//   bigmodel_frame=true  → K_model = sbigmodel_intrinsics（wide → SBIG）
// 输出 3×3 行主序 f32。rpy = [roll, pitch, yaw]（extrinsicsCalibration.rpyCalib）。
void get_warp_matrix(const float rpy[3], const float intrinsics[9], bool bigmodel_frame, float out[9]);

// 相机内参常量：openpilot/common/transformations/camera.py 的 _os_config
//（os04c10，1344×760：narrow_road fl=1522*3/4、wide_road=_os_fisheye fl=567/4*3；
// 与 16 号两路取流分辨率 1344×760 同源）
extern const float kNarrowRoadIntrinsics[9];
extern const float kWideRoadIntrinsics[9];

// GNSS 原始样本（gpsLocationExternal，10 Hz）：单位同 cereal，未编码。
// message_time_ns = 消息 logMonoTime；fill 用上次 fix 的 message_time_ns 与 t_eof 算 age_ms（饱和 4095）。
struct GnssSample {
  double latitude_deg = 0;            // WGS-84
  double longitude_deg = 0;           // WGS-84
  double bearing_deg = 0;             // 0.01° 线上编码；正北顺时针，fill 回绕到 [0, 360)
  double horizontal_accuracy_m = 0;   // 水平精度 1σ（m）
  double bearing_accuracy_deg = 0;    // 航向精度 1σ（°）
  bool has_fix = false;
  bool event_valid = true;      // cereal Event.valid（ubloxd 恒 true，仍按 B5 检查）
  uint64_t message_time_ns = 0;
};

// extrinsicsCalibration.calStatus；只有 invalid 时禁用 yaw/speed，
// uncalibrated / recalibrating 仍用当前 rpy（m-b）。
enum class ExtrinsicsCalibStatus : uint8_t {
  kUncalibrated = 0,
  kCalibrated = 1,
  kInvalid = 2,
  kRecalibrating = 3,
};

// deviceMotion → 车体系样本（helpers.py PoseCalibrator）：
//   device_from_calib = rot_from_euler(rpyCalib)
//   calib_from_device = device_from_calib^T
//   v_body = calib_from_device · velocityDevice
//   w_body = calib_from_device · angularVelocityDevice
// yaw_source_ok  = event_valid ∧ sensorsOK ∧ calStatus != invalid ∧ w_body.z 有限
// speed_source_ok = event_valid ∧ sensorsOK ∧ inputsOK ∧ posenetOK ∧ calStatus != invalid ∧ v_body.x 有限
struct VehicleSample {
  float forward_speed_m_per_s = 0;     // 车体系 x（带符号）
  float lateral_speed_m_per_s = 0;     // 车体系 y
  float yaw_rate_rad_per_s = 0;        // 车体系 z，右转（顺时针）为正，与 bearing 同向
  bool reversing = false;              // forward_speed < −0.3 m/s
  bool standstill = false;             // hypot(forward_speed, lateral_speed) < 0.1 m/s
  bool yaw_source_ok = false;
  bool speed_source_ok = false;
  uint64_t message_time_ns = 0;
};

// deviceMotion 消息的判据快照（I1 / m-b）
struct DeviceMotionFlags {
  bool event_valid = false;        // cereal Event.valid
  bool sensors_ok = false;         // deviceMotion.sensorsOK
  bool inputs_ok = false;          // deviceMotion.inputsOK
  bool posenet_ok = false;         // deviceMotion.posenetOK
  ExtrinsicsCalibStatus calib_status = ExtrinsicsCalibStatus::kUncalibrated;
  uint64_t message_time_ns = 0;
};

VehicleSample vehicle_sample_from_device_motion(const float rpy[3],
                                                const float velocity_device[3],
                                                const float angular_velocity_device[3],
                                                const DeviceMotionFlags& flags);

// 帧头元数据提供者：rpyCalib 由标定线程喂（extrinsicsCalibration），fill() 由取帧线程调。
class MetaProvider {
 public:
  MetaProvider();

  // extrinsicsCalibration.rpyCalib 更新时调用（任意线程）。
  void set_rpy(const float rpy[3]);

  // gpsLocationExternal / deviceMotion 更新时调用（任意线程，只存最新一条）。
  // set_gnss 只在 hasFix 时更新「上次 fix」；set_vehicle 存 deviceMotion 换算后的车体系样本。
  void set_gnss(const GnssSample& s);
  void set_vehicle(const VehicleSample& s);

  // modeld 上行元数据（04 号 C-2，modelDataV2SP）更新时调用（任意线程）：
  // action_t = chestnut 公式（research/02 §4），desire_class = DH.desire 电平。
  void set_model_inputs(const float action_t[2], uint8_t desire_class);

  // 新序列首帧组帧前调用（双路 IDR 恢复/重连 = App 侧 new_seq，bgm1_server.cpp）：
  // MODEL_ABI §4.3 清 previousDesire，持续中的 desire 在新序列首帧重出 pulse。
  void reset_desire_latch();

  // 最近一条 gpsLocationExternal（has_fix 已按 event_valid / 坐标清洗）。false = 从未收到。
  // 票 C 的 MapTargetWriter 取这份快照写 LastGPSPosition，不新增订阅。
  bool latest_gnss(GnssSample* out) const;

  // 生成一帧的头元数据。t_eof = road 帧 timestamp_eof（ns）。
  // 填 t_eof/desire/traffic_convention/action_t/warp_*/gnss/vehicle；flags/frame_idx/road_len/wide_len
  // 归调用方。无样本时 gnss/vehicle 保持缺省（GNSS 全 0、yaw 无效、航向精度 255）。
  void fill(uint64_t t_eof, bgm1::FrameHeader* out) const;

  // 当前 warp 矩阵（wide=false → road/MED，true → wide/SBIG），与 fill 写进帧头的同一份。
  // capture 线程据此在 C4 上 warp（协议 v3）。
  void warp(bool wide, float out[9]) const;

 private:
  mutable std::mutex mtx_;
  float rpy_[3] = {0.f, 0.f, 0.f};
  float warp_road_[9] = {0};
  float warp_wide_[9] = {0};
  float action_t_[2] = {0.f, 0.f};
  uint8_t desire_class_ = 0;
  mutable uint8_t prev_desire_class_ = 0;  // pulse 边沿 latch：每次 fill() 采样
  GnssSample gnss_;                        // 最近一条 gpsLocationExternal（has_fix 已按 event_valid/坐标清洗）
  bool gnss_seen_ = false;
  GnssSample last_fix_;                    // 上次有效 fix（位置/精度/时刻；B3）
  bool have_fix_ = false;
  VehicleSample vehicle_;                  // 最近一条 deviceMotion（车体系）
  bool vehicle_seen_ = false;
};
