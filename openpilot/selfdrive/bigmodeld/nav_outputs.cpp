#include "nav_outputs.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <cstdio>
#include <utility>

namespace {

constexpr float kDmPerS2ToMPerS2 = 0.1f;
constexpr float kCurvatureX1e4ToPerM = 1e-4f;
constexpr float kCentidegToDeg = 0.01f;
constexpr float kDmToM = 0.1f;

constexpr int32_t kDegreesX1e7PerDegree = 10'000'000;
constexpr double kKphToMPerS = 1.0 / 3.6;

// 事件时刻是否在 now 的 max_age 以内（事件时刻在未来 = 时钟回跳，按过期处理，安全侧）
bool within(uint64_t event_ns, uint64_t now_ns, uint64_t max_age_ns) {
  return event_ns != 0 && now_ns >= event_ns && now_ns - event_ns <= max_age_ns;
}

bool coords_ok(double latitude_deg, double longitude_deg) {
  return std::isfinite(latitude_deg) && std::isfinite(longitude_deg) &&
         std::fabs(latitude_deg) <= 90.0 && std::fabs(longitude_deg) <= 180.0;
}

// (0,0) 是合法 WGS-84 坐标，但对本车而言只可能是「未填」；与非法坐标一样跳过（S5）
bool point_coords_ok(int32_t latitude_deg_x1e7, int32_t longitude_deg_x1e7) {
  const double latitude_deg = double(latitude_deg_x1e7) / kDegreesX1e7PerDegree;
  const double longitude_deg = double(longitude_deg_x1e7) / kDegreesX1e7PerDegree;
  return coords_ok(latitude_deg, longitude_deg) && !(latitude_deg == 0.0 && longitude_deg == 0.0);
}

void append_point_json(std::string* out, const bgm1::CurvePoint& p) {
  char point[128];
  const double latitude_deg = double(p.latitude_deg_x1e7) / kDegreesX1e7PerDegree;
  const double longitude_deg = double(p.longitude_deg_x1e7) / kDegreesX1e7PerDegree;
  const double velocity_m_per_s = double(p.target_speed_kph) * kKphToMPerS;
  std::snprintf(point, sizeof point,
                "{\"latitude\":%.7f,\"longitude\":%.7f,\"distance_m\":%u,\"velocity\":%.3f}", latitude_deg,
                longitude_deg, unsigned(p.distance_along_route_m), velocity_m_per_s);
  *out += point;
}

}  // namespace

NavStateSnapshot nav_state_snapshot_from_reply(const bgm1::Reply& reply) {
  NavStateSnapshot s;
  s.frame_index = reply.frame_idx;
  s.timestamp_eof_ns = reply.t_eof;

  const bgm1::NavExt& ext = reply.nav_ext;
  s.phone_location_ok = ext.nav_health_flags & bgm1::kNavHealthPhoneLocationOk;
  s.route_ok = ext.nav_health_flags & bgm1::kNavHealthRouteOk;
  s.nav_ok = ext.nav_health_flags & bgm1::kNavHealthNavOk;
  s.rerouting = ext.nav_health_flags & bgm1::kNavHealthRerouting;
  s.has_nav_state = ext.present_flags & bgm1::kNavPresentNavState;
  s.has_road_attributes = ext.present_flags & bgm1::kNavPresentRoadAttr;
  s.has_curve_ahead = ext.present_flags & bgm1::kNavPresentCurveAhead;
  s.has_nav_debug = ext.present_flags & bgm1::kNavPresentNavDebug;

  const bgm1::NavState& n = ext.nav_state;
  s.nav_state.status = n.nav_status;
  s.nav_state.maneuver_type = n.maneuver_type;
  s.nav_state.next_maneuver_type = n.next_maneuver_type;
  s.nav_state.roundabout_exit_index = n.roundabout_exit_index;
  s.nav_state.distance_to_maneuver_m = n.distance_to_maneuver_m;
  s.nav_state.route_remaining_m = n.route_remaining_m;
  s.nav_state.route_remaining_seconds = n.route_remaining_s;
  s.nav_state.age_ms = n.age_ms;
  s.nav_state.nav_map_fed = n.nav_input_flags & 0x01;
  s.nav_state.navigation_fed = n.nav_input_flags & 0x02;
  s.nav_state.on_route = n.nav_input_flags & 0x04;
  s.nav_state.off_route_distance_m = n.off_route_distance_m;
  s.nav_state.heading_deg = n.heading_centideg == bgm1::kNavU16Invalid
                                ? NAN
                                : float(n.heading_centideg) * kCentidegToDeg;

  const bgm1::RoadAttr& r = ext.road_attr;
  s.road_attributes.speed_limit_kph = r.speed_limit_kph;
  s.road_attributes.speed_limit_source = r.speed_limit_source;
  s.road_attributes.speed_limit_ahead_kph = r.speed_limit_ahead_kph;
  s.road_attributes.speed_limit_ahead_distance_m = r.speed_limit_ahead_distance_m;
  s.road_attributes.road_class = r.road_class;
  s.road_attributes.ramp = r.road_edge_flags & 0x01;
  s.road_attributes.tunnel = r.road_edge_flags & 0x02;
  s.road_attributes.bridge = r.road_edge_flags & 0x04;
  s.road_attributes.roundabout = r.road_edge_flags & 0x08;
  s.road_attributes.toll = r.road_edge_flags & 0x10;
  s.road_attributes.oneway = r.road_edge_flags & 0x20;
  s.road_attributes.match_trusted = r.road_edge_flags & 0x80;
  s.road_attributes.lane_count = r.lane_count;
  s.road_attributes.match_distance_m =
      r.match_distance_dm == bgm1::kNavU16Invalid ? NAN : float(r.match_distance_dm) * kDmToM;
  s.road_attributes.age_ms = r.age_ms;

  const bgm1::CurveAhead& c = ext.curve_ahead;
  s.curve_ahead.point_count = c.point_count;
  s.curve_ahead.max_lateral_acceleration_m_per_s2 =
      float(c.max_lateral_acceleration_dm_per_s2) * kDmPerS2ToMPerS2;
  s.curve_ahead.horizon_distance_m = c.horizon_distance_m;
  s.curve_ahead.age_ms = c.age_ms;
  s.curve_ahead.along_route = c.curve_flags & 0x01;
  s.curve_ahead.road_lookahead = c.curve_flags & 0x02;
  s.curve_ahead.smoothed = c.curve_flags & 0x04;
  for (int i = 0; i < c.point_count && i < int(bgm1::kCurveMaxPoints); i++) {
    // 只读距离 / 目标速度 / 曲率：点经纬度不进快照（决策 17）
    s.curve_ahead.points[i].distance_along_route_m = c.points[i].distance_along_route_m;
    s.curve_ahead.points[i].target_speed_kph = c.points[i].target_speed_kph;
    s.curve_ahead.points[i].curvature_per_m = float(c.points[i].curvature_per_m_x1e4) * kCurvatureX1e4ToPerM;
  }

  const bgm1::NavDebug& d = ext.nav_debug;
  s.nav_debug.render_duration_us = d.render_duration_us;
  s.nav_debug.map_match_duration_us = d.map_match_duration_us;
  s.nav_debug.route_compute_duration_ms = d.route_compute_duration_ms;
  s.nav_debug.nav_cpu_usage_percent = d.nav_cpu_usage_percent;
  return s;
}

void NavLinkCache::on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext) {
  last_reply_ns_ = reply_ns;
  t_eof_ = t_eof;
  present_flags_ = ext.present_flags;
  nav_health_flags_ = ext.nav_health_flags;
  curve_ = ext.curve_ahead;
  nav_state_age_ms_ = ext.nav_state.age_ms;
}

void NavLinkCache::reset() {
  last_reply_ns_ = 0;
  t_eof_ = 0;
  present_flags_ = 0;
  nav_health_flags_ = 0;
  curve_ = bgm1::CurveAhead{};
  nav_state_age_ms_ = bgm1::kNavU16Invalid;
}

bool MapTargetWriter::point_usable(const bgm1::CurvePoint& p) {
  if (p.target_speed_kph == 0 || p.distance_along_route_m > 600) return false;  // 线上「无」：不能当 0 m/s 喂纵向
  return point_coords_ok(p.latitude_deg_x1e7, p.longitude_deg_x1e7);
}

int MapTargetWriter::usable_point_count(const MapTargetInput& in) {
  int n = 0;
  uint16_t previous_distance = 0;
  for (int i = 0; i < in.curve.point_count && i < int(bgm1::kCurveMaxPoints); i++) {
    if (!point_usable(in.curve.points[i])) continue;
    if (n > 0 && in.curve.points[i].distance_along_route_m < previous_distance) return 0;
    previous_distance = in.curve.points[i].distance_along_route_m;
    n++;
  }
  return n;
}

bool MapTargetWriter::urgent_change(const MapTargetInput& in, int n) const {
  (void)n;  // 新集合大小由循环内重算，保持 8×8、无分配
  int new_idx[bgm1::kCurveMaxPoints];
  int m = 0;
  for (int i = 0; i < in.curve.point_count && i < int(bgm1::kCurveMaxPoints); i++) {
    if (point_usable(in.curve.points[i])) new_idx[m++] = i;
  }

  // R5：一对一匹配，每个新点最多匹配一个旧点，避免两个旧点抢同一个新点掩盖删点
  bool used[bgm1::kCurveMaxPoints] = {};
  for (int k = 0; k < last_point_count_; k++) {
    const WrittenPoint& old = last_points_[k];
    int best = -1;
    int64_t best_dist2 = 0;
    for (int j = 0; j < m; j++) {
      if (used[j]) continue;
      const bgm1::CurvePoint& p = in.curve.points[new_idx[j]];
      // R2：先转 int64 再相减，跨 ±180° 不会 int32 溢出
      const int64_t dlat = int64_t(p.latitude_deg_x1e7) - int64_t(old.latitude_deg_x1e7);
      const int64_t dlon = int64_t(p.longitude_deg_x1e7) - int64_t(old.longitude_deg_x1e7);
      if (std::abs(dlat) > kSamePointToleranceX1e7 || std::abs(dlon) > kSamePointToleranceX1e7) {
        continue;
      }
      const int64_t dist2 = dlat * dlat + dlon * dlon;
      // R4：容差内取最近的旧点，而不是第一个命中的
      if (best < 0 || dist2 < best_dist2) {
        best = j;
        best_dist2 = dist2;
      }
    }
    if (best < 0) return true;  // N1 旧点被删除（即使总点数不变）
    used[best] = true;
    // N2：容差内同一点的目标速度下调 → 立即写
    if (in.curve.points[new_idx[best]].target_speed_kph < old.target_speed_kph) return true;
  }
  return false;
}

bool MapTargetWriter::same_points(const MapTargetInput& in, int n) const {
  if (n != last_point_count_) return false;
  if (n == 0) return true;
  int k = 0;
  for (int i = 0; i < in.curve.point_count && i < int(bgm1::kCurveMaxPoints); i++) {
    const bgm1::CurvePoint& p = in.curve.points[i];
    if (!point_usable(p)) continue;
    const WrittenPoint& w = last_points_[k++];
    if (p.latitude_deg_x1e7 != w.latitude_deg_x1e7 ||
        p.longitude_deg_x1e7 != w.longitude_deg_x1e7 ||
        p.target_speed_kph != w.target_speed_kph ||
        p.distance_along_route_m != w.distance_along_route_m) {
      return false;
    }
  }
  return true;
}

std::optional<std::string> MapTargetWriter::update(const MapTargetInput& in) {
  // t_eof 早于手机收齐本帧：只允许提前过期，绝不按 REPLY 到达时刻续命。
  auto sample_time = [&](uint16_t age_ms) -> uint64_t {
    const uint64_t age_ns = uint64_t(age_ms) * 1'000'000ULL;
    if (age_ms == bgm1::kNavU16Invalid || in.t_eof < age_ns || in.t_eof > in.now_ns) return 0;
    const uint64_t sample = in.t_eof - age_ns;
    if (sample > std::numeric_limits<uint64_t>::max() - kNavDataMaxAgeNs) return 0;
    return within(sample, in.now_ns, kNavDataMaxAgeNs) ? sample : 0;
  };
  const uint64_t curve_sample_ns = sample_time(in.curve.age_ms);
  const uint64_t phone_sample_ns = sample_time(in.nav_state_age_ms);
  const bool phone_ok = in.nav_health_flags & bgm1::kNavHealthPhoneLocationOk;
  const bool rerouting = in.nav_health_flags & bgm1::kNavHealthRerouting;
  const bool along_route = in.curve.curve_flags & 0x01;
  const bool route_ok = in.nav_health_flags & bgm1::kNavHealthRouteOk;
  const bool route_usable = !rerouting && (!along_route || route_ok);
  const bool curve_valid = in.link_connected && in.curve_present && in.nav_state_present && phone_ok &&
                           curve_sample_ns != 0 && phone_sample_ns != 0 && route_usable &&
                           in.curve.point_count <= bgm1::kCurveMaxPoints;

  const int n = curve_valid ? usable_point_count(in) : 0;
  const bool same = wrote_ && same_points(in, n);
  const uint64_t since_last_write = in.now_ns > last_write_ns_ ? in.now_ns - last_write_ns_ : 0;
  if (same && since_last_write < kRefreshIntervalNs) return std::nullopt;

  // N1/N2：旧点被删除（含清空）或容差内同一点降速 = 下一拍写（≤1 个 calib 拍）；其余变化仍 ≤2 Hz
  const bool urgent = wrote_ && (n == 0 ? last_point_count_ > 0 : urgent_change(in, n));
  if (!same && !urgent && wrote_ && since_last_write < kMinChangedIntervalNs) {
    return std::nullopt;
  }

  std::string json = "[]";
  if (n > 0) {
    const uint64_t valid_until_ns = std::min(curve_sample_ns, phone_sample_ns) + kNavDataMaxAgeNs;
    json = "{\"sample_time_ns\":" + std::to_string(curve_sample_ns) +
           ",\"valid_until_ns\":" + std::to_string(valid_until_ns) + ",\"points\":[";
    bool first = true;
    for (int i = 0; i < in.curve.point_count; i++) {
      if (!point_usable(in.curve.points[i])) continue;
      if (!first) json += ',';
      first = false;
      append_point_json(&json, in.curve.points[i]);
    }
    json += "]}";
  }

  wrote_ = true;
  last_write_ns_ = in.now_ns;
  last_point_count_ = 0;
  if (n > 0) {
    for (int i = 0; i < in.curve.point_count && i < int(bgm1::kCurveMaxPoints); i++) {
      const bgm1::CurvePoint& p = in.curve.points[i];
      if (!point_usable(p)) continue;
      WrittenPoint& w = last_points_[last_point_count_++];
      w.latitude_deg_x1e7 = p.latitude_deg_x1e7;
      w.longitude_deg_x1e7 = p.longitude_deg_x1e7;
      w.target_speed_kph = p.target_speed_kph;
      w.distance_along_route_m = p.distance_along_route_m;
    }
  }
  return json;
}

void NavLinkSession::decide(bool link_connected, const NavLinkTick& tick) {
  MapTargetInput in;
  in.link_connected = link_connected;
  in.curve_present = cache_.present_flags() & bgm1::kNavPresentCurveAhead;
  in.curve = cache_.curve();
  in.nav_state_present = cache_.present_flags() & bgm1::kNavPresentNavState;
  in.nav_state_age_ms = cache_.nav_state_age_ms();
  in.nav_health_flags = cache_.nav_health_flags();
  in.last_reply_ns = cache_.last_reply_ns();
  // N7：t_eof 与 nanos_since_boot 同为 CLOCK_BOOTTIME（camerad spectra.cc 用
  // nanos_since_boot() − timestamp_eof 算 processing_time；SOF 时间戳来自内核）
  in.t_eof = cache_.t_eof();
  in.now_ns = tick.now_ns;
  if (auto json = writer_.update(in)) pending_ = std::move(json);
}

void NavLinkSession::on_disconnect(const NavLinkTick& tick) {
  // S2：断连即丢弃 NavExt 摘要；旧弯道点不得继续驱动纵向（MapTargetVelocities → []）
  hello_seen_ = false;
  cache_.reset();
  decide(/*link_connected=*/false, tick);
}

void NavLinkSession::on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext,
                              const NavLinkTick& tick) {
  cache_.on_reply(reply_ns, t_eof, ext);
  decide(/*link_connected=*/true, tick);
}

void NavLinkSession::on_idle(bool connected, const NavLinkTick& tick) {
  if (!connected) {
    on_disconnect(tick);  // 未连接/启动即清空：不留下上一趟或本趟崩溃前的弯道点
    return;
  }
  decide(/*link_connected=*/true, tick);
}

void clear_map_targets(const MapParamsOps& ops) {
  ops.put("MapTargetVelocities", "[]");
}
