#include "frame_meta.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace {

// 3×3 行主序 double 小工具（仅本文件用）
void mat_mul(const double* a, const double* b, double* out) {
  double t[9];
  for (int r = 0; r < 3; r++) {
    for (int c = 0; c < 3; c++) {
      t[3 * r + c] = a[3 * r + 0] * b[0 + c] + a[3 * r + 1] * b[3 + c] + a[3 * r + 2] * b[6 + c];
    }
  }
  std::memcpy(out, t, sizeof t);
}

bool mat_inv(const double* m, double* out) {
  const double a = m[0], b = m[1], c = m[2];
  const double d = m[3], e = m[4], f = m[5];
  const double g = m[6], h = m[7], i = m[8];
  const double A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const double det = a * A + b * B + c * C;
  if (det == 0.0) return false;
  const double inv = 1.0 / det;
  out[0] = A * inv;
  out[1] = -(b * i - c * h) * inv;
  out[2] = (b * f - c * e) * inv;
  out[3] = B * inv;
  out[4] = (a * i - c * g) * inv;
  out[5] = -(a * f - c * d) * inv;
  out[6] = C * inv;
  out[7] = -(a * h - b * g) * inv;
  out[8] = (a * e - b * d) * inv;
  return true;
}

// transformations.py:141 euler2rot_single：Rz(yaw) @ Ry(pitch) @ Rx(roll)（Z-Y-X）
void rot_from_euler(const double euler[3], double* out) {
  const double phi = euler[0], theta = euler[1], psi = euler[2];  // roll, pitch, yaw
  const double cx = std::cos(phi), sx = std::sin(phi);
  const double cy = std::cos(theta), sy = std::sin(theta);
  const double cz = std::cos(psi), sz = std::sin(psi);
  const double rx[9] = {1, 0, 0, 0, cx, -sx, 0, sx, cx};
  const double ry[9] = {cy, 0, sy, 0, 1, 0, -sy, 0, cy};
  const double rz[9] = {cz, -sz, 0, sz, cz, 0, 0, 0, 1};
  double t[9];
  mat_mul(rz, ry, t);
  mat_mul(t, rx, out);
}

// camera.py:65-66 view_frame_from_device_frame = device_frame_from_view_frame.T
//（device x→forward,y→right,z→down；view x→right,y→down,z→forward）
const double kViewFromDevice[9] = {0, 1, 0,
                                   0, 0, 1,
                                   1, 0, 0};

// model.py:14-18 medmodel_intrinsics（MEDMODEL_CY = 47.6）
const double kMedmodelIntrinsics[9] = {910, 0, 256,
                                       0, 910, 47.6,
                                       0, 0, 1};

// model.py:36-40 sbigmodel_intrinsics（cy = 0.5 * (256 + 47.6) = 151.8）
const double kSbigmodelIntrinsics[9] = {455, 0, 256,
                                        0, 455, 151.8,
                                        0, 0, 1};

// ---- GNSS / VEHICLE 编码（协议 v3 §1.2.1/§1.2.2）----
constexpr uint64_t kNsPerMs = 1'000'000;
constexpr uint32_t kFixMaxAgeMs = 2000;             // has_fix = hasFix ∧ age ≤ 2000 ms
constexpr uint64_t kDeviceMotionMaxAgeNs = 100'000'000;  // 2 个 20 Hz FRAME
constexpr double kPi = 3.14159265358979323846;
constexpr double kRadPerSToCentidegPerS = 180.0 * 100.0 / kPi;  // rad/s → 0.01 °/s（右转/顺时针为正）

bool coords_ok(double lat, double lon) {
  return std::isfinite(lat) && std::isfinite(lon) && std::fabs(lat) <= 90.0 && std::fabs(lon) <= 180.0;
}

// 非有限值 → 0；范围不在此检查，靠调用前的 coords_ok 把关。
int32_t degrees_to_x1e7(double deg) {
  if (!std::isfinite(deg)) return 0;
  return int32_t(std::llround(deg * 1e7));
}

void mat_transpose(const double m[9], double out[9]) {
  for (int r = 0; r < 3; r++) {
    for (int c = 0; c < 3; c++) out[3 * r + c] = m[3 * c + r];
  }
}

void mat_vec(const double m[9], const float v[3], double out[3]) {
  for (int r = 0; r < 3; r++) out[r] = m[3 * r + 0] * v[0] + m[3 * r + 1] * v[1] + m[3 * r + 2] * v[2];
}

// 非有限值 → 0；否则先 fmod 到 (-360, 360)，负值 +360°；四舍五入得到 36000 时折回 0，结果落在 [0, 36000)。
uint16_t degrees_to_centidegrees(double deg) {
  if (!std::isfinite(deg)) return 0;
  double d = std::fmod(deg, 360.0);
  if (d < 0) d += 360.0;
  uint32_t cd = uint32_t(std::lround(d * 100.0));
  if (cd >= 36000) cd -= 36000;  // 359.999° 四舍五入到 360.00 → 0
  return uint16_t(cd);
}

// 非有限或 ≤0 → kGnssHorizontalAccuracyUnknownDm（0xFFFF）；<0.05 m 钳到 1 dm；
// ≥ kGnssHorizontalAccuracyMaxDm/10（6553.4 m）饱和 0xFFFE（先饱和再四舍五入，避免溢出）。
uint16_t meters_to_decimeters(double m) {
  if (!std::isfinite(m) || m <= 0) return bgm1::kGnssHorizontalAccuracyUnknownDm;   // m2：≤0 视为未知
  if (m < 0.05) return 1;                                       // m2：车机永不写 0 dm
  if (m >= bgm1::kGnssHorizontalAccuracyMaxDm / 10.0) return bgm1::kGnssHorizontalAccuracyMaxDm;  // 先饱和，避免 lround 溢出
  return uint16_t(std::lround(m * 10.0));
}

// 非有限值 → 0；先取绝对值再换 cm/s，≥ 65535 饱和。
uint16_t m_per_s_to_cm_per_s(float m_per_s) {
  if (!std::isfinite(m_per_s)) return 0;
  const double cm_per_s = std::fabs(m_per_s) * 100.0;
  if (cm_per_s >= 65535.0) return 65535;
  return uint16_t(std::lround(cm_per_s));
}

// 非有限值 → INT16_MIN（无效哨兵）；结果钳在 ±32767，避开该哨兵。
int16_t rad_per_s_to_centideg_per_s(float rad_per_s) {
  if (!std::isfinite(rad_per_s)) return bgm1::kYawRateInvalidCentidegPerS;
  const double centideg_per_s = double(rad_per_s) * kRadPerSToCentidegPerS;
  if (centideg_per_s >= 32767.0) return 32767;
  if (centideg_per_s <= -32767.0) return -32767;  // 避开 INT16_MIN（无效哨兵）
  return int16_t(std::lround(centideg_per_s));
}

// 非有限或 ≤0 → 255（未知）；<0.25° 钳到 1（不写 0）；≥ 127° 饱和 254。
uint8_t degrees_to_half_degrees(double deg) {
  if (!std::isfinite(deg) || deg <= 0) return bgm1::kGnssBearingAccuracyUnknownHalfDeg;  // m2：≤0 视为未知
  if (deg < 0.25) return 1;                                                            // m2：不写 0 half_deg
  if (deg >= bgm1::kGnssBearingAccuracyMaxHalfDeg * 0.5) return bgm1::kGnssBearingAccuracyMaxHalfDeg;    // 127° 以上饱和
  return uint8_t(std::lround(deg / 0.5));
}

}  // namespace

VehicleSample vehicle_sample_from_device_motion(const float rpy[3],
                                                const float velocity_device[3],
                                                const float angular_velocity_device[3],
                                                const DeviceMotionFlags& flags) {
  const double euler[3] = {rpy[0], rpy[1], rpy[2]};
  double device_from_calib[9], calib_from_device[9];
  rot_from_euler(euler, device_from_calib);
  mat_transpose(device_from_calib, calib_from_device);

  double v_body[3], w_body[3];
  mat_vec(calib_from_device, velocity_device, v_body);
  mat_vec(calib_from_device, angular_velocity_device, w_body);

  VehicleSample s;
  s.forward_speed_m_per_s = float(v_body[0]);
  s.lateral_speed_m_per_s = float(v_body[1]);
  // 车体系 FRD 的 z 轴向下，正 z 角速度 = 从上往下看顺时针 = 右转，与 bearing 同向。
  // 取 w_body.z 不取反；INT16_MIN 仍是无效哨兵（由 fill 的 rad_per_s_to_centideg_per_s 处理）。
  s.yaw_rate_rad_per_s = float(w_body[2]);
  const bool calib_ok = flags.calib_status != ExtrinsicsCalibStatus::kInvalid;
  s.yaw_source_ok = flags.event_valid && flags.sensors_ok && calib_ok && std::isfinite(s.yaw_rate_rad_per_s);
  s.speed_source_ok = flags.event_valid && flags.sensors_ok && flags.inputs_ok && flags.posenet_ok &&
                      calib_ok && std::isfinite(s.forward_speed_m_per_s);
  s.reversing = s.forward_speed_m_per_s < -0.3f;
  s.standstill = std::hypot(s.forward_speed_m_per_s, s.lateral_speed_m_per_s) < 0.1f;
  s.message_time_ns = flags.message_time_ns;
  return s;
}

const float kNarrowRoadIntrinsics[9] = {1141.5f, 0.f, 672.f,
                                        0.f, 1141.5f, 380.f,
                                        0.f, 0.f, 1.f};
const float kWideRoadIntrinsics[9] = {425.25f, 0.f, 672.f,
                                      0.f, 425.25f, 380.f,
                                      0.f, 0.f, 1.f};

void get_warp_matrix(const float rpy[3], const float intrinsics[9], bool bigmodel_frame, float out[9]) {
  const double euler[3] = {rpy[0], rpy[1], rpy[2]};
  double rot[9];
  rot_from_euler(euler, rot);

  double k_cam[9], k_model[9];
  for (int i = 0; i < 9; i++) {
    k_cam[i] = intrinsics[i];
    k_model[i] = bigmodel_frame ? kSbigmodelIntrinsics[i] : kMedmodelIntrinsics[i];
  }

  double tmp[9], camera_from_calib[9], calib_from_model[9], warp[9];
  mat_mul(k_cam, kViewFromDevice, tmp);
  mat_mul(tmp, rot, camera_from_calib);            // camera_from_calib = K_cam @ V @ R
  mat_mul(k_model, kViewFromDevice, tmp);
  if (!mat_inv(tmp, calib_from_model)) {           // calib_from_model = inv(K_model @ V)
    for (int i = 0; i < 9; i++) out[i] = 0.f;
    return;
  }
  mat_mul(camera_from_calib, calib_from_model, warp);

  for (int i = 0; i < 9; i++) out[i] = (float)warp[i];
}

MetaProvider::MetaProvider() {
  get_warp_matrix(rpy_, kNarrowRoadIntrinsics, false, warp_road_);
  get_warp_matrix(rpy_, kWideRoadIntrinsics, true, warp_wide_);
}

void MetaProvider::warp(bool wide, float out[9]) const {
  std::lock_guard<std::mutex> lk(mtx_);
  std::memcpy(out, wide ? warp_wide_ : warp_road_, sizeof warp_road_);
}

void MetaProvider::set_rpy(const float rpy[3]) {
  // AU-12：两套 warp 矩阵在锁外算好，锁内只拷贝。calib 线程（SCHED_OTHER）持锁被抢占时，
  // 等锁的 capture（FIFO 50）/ reply（FIFO 53）线程不会因 mutex 无优先级继承而被拖住。
  float warp_road[9], warp_wide[9];
  get_warp_matrix(rpy, kNarrowRoadIntrinsics, false, warp_road);
  get_warp_matrix(rpy, kWideRoadIntrinsics, true, warp_wide);
  std::lock_guard<std::mutex> lk(mtx_);
  std::memcpy(rpy_, rpy, sizeof rpy_);
  std::memcpy(warp_road_, warp_road, sizeof warp_road_);
  std::memcpy(warp_wide_, warp_wide, sizeof warp_wide_);
}

void MetaProvider::set_model_inputs(const float action_t[2], uint8_t desire_class) {
  std::lock_guard<std::mutex> lk(mtx_);
  action_t_[0] = action_t[0];
  action_t_[1] = action_t[1];
  desire_class_ = desire_class;
}

void MetaProvider::set_gnss(const GnssSample& s) {
  // B5：gpsLocationExternal.valid 也检查（ubloxd 恒 true）；
  // m3：坐标非有限/越界时这条消息不算 fix，也不更新「上次 fix」。
  GnssSample g = s;
  g.has_fix = s.event_valid && s.has_fix && coords_ok(s.latitude_deg, s.longitude_deg);
  std::lock_guard<std::mutex> lk(mtx_);
  gnss_ = g;
  gnss_seen_ = true;
  if (g.has_fix) {
    last_fix_ = g;  // B3：只有 hasFix=true 才更新上次 fix 的时刻与位置
    have_fix_ = true;
  }
}

void MetaProvider::set_vehicle(const VehicleSample& s) {
  std::lock_guard<std::mutex> lk(mtx_);
  vehicle_ = s;
  vehicle_seen_ = true;
}

bool MetaProvider::latest_gnss(GnssSample* out) const {
  std::lock_guard<std::mutex> lk(mtx_);
  if (!gnss_seen_) return false;
  *out = gnss_;
  return true;
}

void MetaProvider::reset_desire_latch() {
  std::lock_guard<std::mutex> lk(mtx_);
  prev_desire_class_ = 0;
}

void MetaProvider::fill(uint64_t t_eof, bgm1::FrameHeader* out) const {
  // m6：锁内只拷样本快照（desire pulse 的 latch 也在此更新），换算全在锁外。
  GnssSample gnss, last_fix;
  bool gnss_seen, have_fix;
  VehicleSample veh;
  bool veh_seen;
  float action_t[2];
  float warp_road[9], warp_wide[9];
  float desire[8] = {0};
  {
    std::lock_guard<std::mutex> lk(mtx_);
    // desire 电平 → pulse 边沿（MODEL_ABI §4.2）：d!=0 且换值才 one-hot 一次，通道 0 恒 0。
    const uint8_t d = desire_class_;
    if (d != 0 && d != prev_desire_class_ && d < 8) desire[d] = 1.f;
    prev_desire_class_ = d;

    gnss = gnss_;
    gnss_seen = gnss_seen_;
    last_fix = last_fix_;
    have_fix = have_fix_;
    veh = vehicle_;
    veh_seen = vehicle_seen_;
    std::memcpy(action_t, action_t_, sizeof action_t);
    std::memcpy(warp_road, warp_road_, sizeof warp_road);
    std::memcpy(warp_wide, warp_wide_, sizeof warp_wide);
  }

  out->t_eof = t_eof;
  std::memcpy(out->desire, desire, sizeof desire);
  out->traffic_convention[0] = 1.f;                           // lean modeld is_rhd=False → [1, 0]
  out->traffic_convention[1] = 0.f;
  out->action_t[0] = action_t[0];
  out->action_t[1] = action_t[1];
  std::memcpy(out->warp_road, warp_road, sizeof warp_road);
  std::memcpy(out->warp_wide, warp_wide, sizeof warp_wide);

  // VEHICLE：deviceMotion 100 ms 内 + 消息 event_valid/sensorsOK + 分量有限；yaw/speed 各自判。
  out->gnss = bgm1::Gnss{};
  out->vehicle = bgm1::Vehicle{};
  const bool veh_fresh = veh_seen && veh.message_time_ns != 0 &&
                         t_eof <= veh.message_time_ns + kDeviceMotionMaxAgeNs;
  const bool speed_valid = veh_fresh && veh.speed_source_ok && std::isfinite(veh.forward_speed_m_per_s);
  const bool yaw_valid = veh_fresh && veh.yaw_source_ok && std::isfinite(veh.yaw_rate_rad_per_s);
  uint8_t vehicle_state_flags = 0;
  if (speed_valid) {
    out->gnss.speed_cm_per_s = m_per_s_to_cm_per_s(veh.forward_speed_m_per_s);
    if (veh.reversing) vehicle_state_flags |= bgm1::kVehicleReverse;
    if (veh.standstill) vehicle_state_flags |= bgm1::kVehicleStandstill;
  }
  if (yaw_valid) {
    out->vehicle.yaw_rate_centideg_per_s = rad_per_s_to_centideg_per_s(veh.yaw_rate_rad_per_s);
    if (out->vehicle.yaw_rate_centideg_per_s != bgm1::kYawRateInvalidCentidegPerS) vehicle_state_flags |= bgm1::kVehicleYawValid;
  }
  out->vehicle.vehicle_state_flags = vehicle_state_flags;

  // GNSS：age 是「上次 fix」的年龄（B3）；never fix 但 speed_valid → 4095。
  uint32_t age_ms = 0;
  bool has_fix = false;
  if (have_fix) {
    const uint64_t age_ns = t_eof > last_fix.message_time_ns ? t_eof - last_fix.message_time_ns : 0;
    age_ms = uint32_t(std::min<uint64_t>(age_ns / kNsPerMs, bgm1::kGnssAgeMax));
    has_fix = gnss_seen && gnss.has_fix && age_ms <= kFixMaxAgeMs;
  } else if (speed_valid) {
    age_ms = bgm1::kGnssAgeMax;
  }

  bool bearing_valid = false;
  if (has_fix) {
    // 无 fix 时位置类字段的清零与 bearing_accuracy_half_deg=255 由 pack 写规范形（票 A），此处不重复。
    out->gnss.latitude_deg_x1e7 = degrees_to_x1e7(last_fix.latitude_deg);
    out->gnss.longitude_deg_x1e7 = degrees_to_x1e7(last_fix.longitude_deg);
    out->gnss.bearing_centideg = degrees_to_centidegrees(last_fix.bearing_deg);
    out->gnss.horizontal_accuracy_dm = meters_to_decimeters(last_fix.horizontal_accuracy_m);
    out->vehicle.bearing_accuracy_half_deg = degrees_to_half_degrees(last_fix.bearing_accuracy_deg);
    bearing_valid = std::isfinite(last_fix.bearing_deg);
  }
  out->gnss.fix_age_and_valid_flags = bgm1::make_fix_age_and_valid_flags(age_ms, has_fix, speed_valid, bearing_valid);
}
