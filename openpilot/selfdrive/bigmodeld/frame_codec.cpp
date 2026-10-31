#include "frame_codec.h"

#include <cstring>

namespace bgm1 {
namespace {

inline void put_u16(uint8_t* p, uint16_t v) {
  p[0] = uint8_t(v);
  p[1] = uint8_t(v >> 8);
}
inline void put_u32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v);
  p[1] = uint8_t(v >> 8);
  p[2] = uint8_t(v >> 16);
  p[3] = uint8_t(v >> 24);
}
inline void put_u64(uint8_t* p, uint64_t v) {
  put_u32(p, uint32_t(v));
  put_u32(p + 4, uint32_t(v >> 32));
}
inline uint16_t get_u16(const uint8_t* p) {
  return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}
inline void put_i16(uint8_t* p, int16_t v) { put_u16(p, uint16_t(v)); }
inline void put_i32(uint8_t* p, int32_t v) { put_u32(p, uint32_t(v)); }
inline uint32_t get_u32(const uint8_t* p) {
  return read_u32_le(p);  // 实现收在 frame_codec.h（bgm1_server 等 streaming 消费方共用）
}
inline int16_t get_i16(const uint8_t* p) { return int16_t(get_u16(p)); }
inline int32_t get_i32(const uint8_t* p) { return int32_t(get_u32(p)); }
inline uint64_t get_u64(const uint8_t* p) {
  return uint64_t(get_u32(p)) | (uint64_t(get_u32(p + 4)) << 32);
}

inline void put_f32(uint8_t* p, float v) {
  uint32_t bits;
  std::memcpy(&bits, &v, 4);
  put_u32(p, bits);
}
inline float get_f32(const uint8_t* p) {
  uint32_t bits = get_u32(p);
  float v;
  std::memcpy(&v, &bits, 4);
  return v;
}

// 通用 MsgHdr 校验（magic/ver/type），REPLY/ERR 解析共用
Err parse_msg_hdr_typed(const uint8_t in[kMsgHdrSize], uint8_t expect_type,
                        uint16_t* flags, uint32_t* frame_idx, uint32_t* len) {
  if (get_u32(in) != kMagic) return Err::kBadMagic;
  if (in[4] != kVersion) return Err::kBadVersion;
  if (in[5] != expect_type) return Err::kBadType;
  if (flags) *flags = get_u16(in + 6);
  if (frame_idx) *frame_idx = get_u32(in + 8);
  if (len) *len = get_u32(in + 12);
  return Err::kOk;
}

void put_msg_hdr(uint8_t out[kMsgHdrSize], uint8_t type, uint16_t flags,
                 uint32_t frame_idx, uint32_t len) {
  std::memset(out, 0, kMsgHdrSize);
  put_u32(out + 0, kMagic);
  out[4] = kVersion;
  out[5] = type;
  put_u16(out + 6, flags);
  put_u32(out + 8, frame_idx);
  put_u32(out + 12, len);
}

// 规格保留位的屏蔽：pack 把这些位强制写 0（调用方给的保留位不上线）
constexpr uint8_t kVehicleStateFlagsMask = kVehicleYawValid | kVehicleReverse | kVehicleStandstill | kVehicleSpeedValid;
constexpr uint8_t kNavPresentMask = 0x0F;
constexpr uint8_t kNavHealthMask = 0x0F;
constexpr uint8_t kNavInputFlagsMask = 0x0F;   // 清 bit4–7
constexpr uint8_t kRoadEdgeFlagsMask = 0xBF;   // 清 bit6
constexpr uint8_t kCurveFlagsMask = 0x07;      // 清 bit3–7

}  // namespace

void pack_frame_header(const FrameHeader& hdr, uint8_t out[kFrameHdrSize]) {
  std::memset(out, 0, kFrameHdrSize);
  put_msg_hdr(out, kTypeFrame, hdr.flags, hdr.frame_idx, hdr.road_len);
  put_u64(out + 16, hdr.t_eof);
  for (int i = 0; i < 8; i++) put_f32(out + 24 + 4 * i, hdr.desire[i]);
  for (int i = 0; i < 2; i++) put_f32(out + 56 + 4 * i, hdr.traffic_convention[i]);
  for (int i = 0; i < 2; i++) put_f32(out + 64 + 4 * i, hdr.action_t[i]);
  for (int i = 0; i < 9; i++) put_f32(out + 72 + 4 * i, hdr.warp_road[i]);
  for (int i = 0; i < 9; i++) put_f32(out + 108 + 4 * i, hdr.warp_wide[i]);
  const auto& vehicle = hdr.vehicle;
  const bool yaw_ok = vehicle.yaw_valid() && vehicle.yaw_rate_centideg_per_s != kYawRateInvalidCentidegPerS;
  uint8_t flags = vehicle.vehicle_state_flags & kVehicleStateFlagsMask;
  if (!yaw_ok) flags &= uint8_t(~kVehicleYawValid);
  if (!vehicle.speed_valid()) flags &= uint8_t(~(kVehicleReverse | kVehicleStandstill));
  put_u16(out + 144, vehicle.speed_valid() ? vehicle.speed_cm_per_s : 0);
  put_i16(out + 146, yaw_ok ? vehicle.yaw_rate_centideg_per_s : kYawRateInvalidCentidegPerS);
  out[148] = flags;

}

Err parse_msg_hdr(const uint8_t in[kMsgHdrSize], uint8_t* type, uint16_t* flags,
                  uint32_t* frame_idx, uint32_t* len) {
  Err err = parse_msg_hdr_typed(in, kTypeFrame, flags, frame_idx, len);
  if (type) *type = in[5];
  return err;
}

Err parse_frame_header(const uint8_t in[kFrameHdrSize], FrameHeader* out) {
  uint8_t type;
  uint16_t flags;
  uint32_t frame_idx, road_len;
  Err err = parse_msg_hdr(in, &type, &flags, &frame_idx, &road_len);
  if (err != Err::kOk) return err;

  FrameHeader h;
  h.flags = flags;
  h.frame_idx = frame_idx;
  h.road_len = road_len;
  h.t_eof = get_u64(in + 16);
  for (int i = 0; i < 8; i++) h.desire[i] = get_f32(in + 24 + 4 * i);
  for (int i = 0; i < 2; i++) h.traffic_convention[i] = get_f32(in + 56 + 4 * i);
  for (int i = 0; i < 2; i++) h.action_t[i] = get_f32(in + 64 + 4 * i);
  for (int i = 0; i < 9; i++) h.warp_road[i] = get_f32(in + 72 + 4 * i);
  for (int i = 0; i < 9; i++) h.warp_wide[i] = get_f32(in + 108 + 4 * i);
  h.vehicle.speed_cm_per_s = get_u16(in + 144);
  h.vehicle.yaw_rate_centideg_per_s = get_i16(in + 146);
  h.vehicle.vehicle_state_flags = in[148] & kVehicleStateFlagsMask;
  if (!h.vehicle.speed_valid()) {
    h.vehicle.speed_cm_per_s = 0;
    h.vehicle.vehicle_state_flags &= uint8_t(~(kVehicleReverse | kVehicleStandstill));
  }
  if (!h.vehicle.yaw_valid() || h.vehicle.yaw_rate_centideg_per_s == kYawRateInvalidCentidegPerS) {
    h.vehicle.yaw_rate_centideg_per_s = kYawRateInvalidCentidegPerS;
    h.vehicle.vehicle_state_flags &= uint8_t(~kVehicleYawValid);
  }
  if (out) *out = h;
  return Err::kOk;
}

size_t pack_frame(const FrameHeader& hdr, const uint8_t* road, const uint8_t* wide,
                  uint8_t* out, size_t cap) {
  const size_t total = frame_wire_size(hdr.road_len, hdr.wide_len);
  if (cap < total) return 0;
  pack_frame_header(hdr, out);
  std::memcpy(out + kFrameHdrSize, road, hdr.road_len);
  put_u32(out + kFrameHdrSize + hdr.road_len, hdr.wide_len);
  std::memcpy(out + kFrameHdrSize + hdr.road_len + kWideLenSize, wide, hdr.wide_len);
  std::memset(out + kFrameHdrSize + hdr.road_len + kWideLenSize + hdr.wide_len, 0, kTailReservedSize);  // 尾部保留 16 B
  return total;
}

Err parse_frame(const uint8_t* data, size_t size, FrameView* out,
                uint32_t max_segment) {
  if (size < kMsgHdrSize) return Err::kTruncated;
  Err header_err = parse_msg_hdr(data, nullptr, nullptr, nullptr, nullptr);
  if (header_err != Err::kOk) return header_err;
  if (size < kFrameMinSize) return Err::kTruncated;

  FrameHeader h;
  Err err = parse_frame_header(data, &h);
  if (err != Err::kOk) return err;

  // 载荷长度：先按声明校验上限（坏 road_len 不应误报截断），再校验实际线长
  if (h.road_len > max_segment) return Err::kLenTooLarge;
  if (size < kFrameHdrSize + h.road_len + kWideLenSize) return Err::kTruncated;
  const uint32_t wide_len = get_u32(data + kFrameHdrSize + h.road_len);
  if (wide_len > max_segment) return Err::kLenTooLarge;
  h.wide_len = wide_len;
  if (size != frame_wire_size(h.road_len, wide_len)) return Err::kLenMismatch;

  if (out) {
    out->hdr = h;
    out->road = data + kFrameHdrSize;
    out->wide = data + kFrameHdrSize + h.road_len + kWideLenSize;
  }
  return Err::kOk;
}

namespace {

// NavExt 子结构偏移（相对 NavExt 起点 kNavExtOffset = REPLY 偏移 8304）
constexpr size_t kNavStateOffset = 4, kRoadAttrOffset = 28, kCurveAheadOffset = 44, kNavDebugOffset = 148;
constexpr size_t kCurvePointsOffset = 8, kCurvePointSize = 12;

size_t clamp_curve_count(uint8_t point_count) { return point_count > kCurveMaxPoints ? kCurveMaxPoints : point_count; }

void pack_nav_ext(const NavExt& n, uint8_t* p) {  // p 已清零；present_flags 为 0 的子结构保持 0
  const uint8_t present_flags = n.present_flags & kNavPresentMask;  // 保留位不上线
  p[0] = present_flags;
  p[1] = n.nav_health_flags & kNavHealthMask;
  if (present_flags & kNavPresentNavState) {
    uint8_t* q = p + kNavStateOffset;
    const NavState& s = n.nav_state;
    q[0] = s.nav_status;
    q[1] = s.maneuver_type;
    q[2] = s.next_maneuver_type;
    q[3] = s.roundabout_exit_index;
    put_u32(q + 4, s.distance_to_maneuver_m);
    put_u32(q + 8, s.route_remaining_m);
    put_u32(q + 12, s.route_remaining_s);
    put_u16(q + 16, s.age_ms);
    q[18] = s.nav_input_flags & kNavInputFlagsMask;
    q[19] = s.off_route_distance_m;
    put_u16(q + 20, s.heading_centideg);
  }
  if (present_flags & kNavPresentRoadAttr) {
    uint8_t* q = p + kRoadAttrOffset;
    const RoadAttr& r = n.road_attr;
    q[0] = r.speed_limit_kph;
    q[1] = r.speed_limit_source;
    q[2] = r.speed_limit_ahead_kph;
    q[3] = r.road_class;
    put_u16(q + 4, r.speed_limit_ahead_distance_m);
    put_u16(q + 6, r.age_ms);
    q[8] = r.road_edge_flags & kRoadEdgeFlagsMask;
    q[9] = r.lane_count;
    put_u16(q + 12, r.match_distance_dm);
  }
  if (present_flags & kNavPresentCurveAhead) {
    uint8_t* q = p + kCurveAheadOffset;
    const CurveAhead& c = n.curve_ahead;
    const size_t point_count = clamp_curve_count(c.point_count);
    q[0] = uint8_t(point_count);
    q[1] = c.max_lateral_acceleration_dm_per_s2;
    put_u16(q + 2, c.horizon_distance_m);
    put_u16(q + 4, c.age_ms);
    q[6] = c.curve_flags & kCurveFlagsMask;
    for (size_t i = 0; i < point_count; i++) {
      uint8_t* pt = q + kCurvePointsOffset + kCurvePointSize * i;
      put_i32(pt, c.points[i].latitude_deg_x1e7);
      put_i32(pt + 4, c.points[i].longitude_deg_x1e7);
      put_u16(pt + 8, c.points[i].distance_along_route_m);
      pt[10] = c.points[i].target_speed_kph;
      pt[11] = c.points[i].curvature_per_m_x1e4;
    }
  }
  if (present_flags & kNavPresentNavDebug) {
    uint8_t* q = p + kNavDebugOffset;
    put_u16(q, n.nav_debug.render_duration_us);
    put_u16(q + 2, n.nav_debug.map_match_duration_us);
    put_u16(q + 4, n.nav_debug.route_compute_duration_ms);
    q[6] = n.nav_debug.nav_cpu_usage_percent;
  }
}

// 原地填充 *out。*out 必须是刚构造的缺省值：present_flags 为 0 的子结构、坏块（CURVE point_count>8）靠「保持缺省」
// 表达，这里不再逐字段清零（省拷贝）。以后若让 parse_reply 直接写调用方的 *out（不经局部 Reply），
// 必须先把 *out 的 nav_ext 清零。
void parse_nav_ext(const uint8_t* p, NavExt* out) {
  NavExt& n = *out;
  n.present_flags = p[0];
  n.nav_health_flags = p[1];
  if (n.present_flags & kNavPresentNavState) {
    const uint8_t* q = p + kNavStateOffset;
    NavState& s = n.nav_state;
    s.nav_status = q[0];
    s.maneuver_type = q[1];
    s.next_maneuver_type = q[2];
    s.roundabout_exit_index = q[3];
    s.distance_to_maneuver_m = get_u32(q + 4);
    s.route_remaining_m = get_u32(q + 8);
    s.route_remaining_s = get_u32(q + 12);
    s.age_ms = get_u16(q + 16);
    s.nav_input_flags = q[18];
    s.off_route_distance_m = q[19];
    s.heading_centideg = get_u16(q + 20);
  }
  if (n.present_flags & kNavPresentRoadAttr) {
    const uint8_t* q = p + kRoadAttrOffset;
    RoadAttr& r = n.road_attr;
    r.speed_limit_kph = q[0];
    r.speed_limit_source = q[1];
    r.speed_limit_ahead_kph = q[2];
    r.road_class = q[3];
    r.speed_limit_ahead_distance_m = get_u16(q + 4);
    r.age_ms = get_u16(q + 6);
    r.road_edge_flags = q[8];
    r.lane_count = q[9];
    r.match_distance_dm = get_u16(q + 12);
  }
  // 线上 point_count>8 = 坏块（这些点要喂纵向）：清 present_flags bit2，子块整块按缺省（与 present_flags=0 统一：其余字段无意义）
  if ((n.present_flags & kNavPresentCurveAhead) && p[kCurveAheadOffset] > kCurveMaxPoints) n.present_flags &= uint8_t(~kNavPresentCurveAhead);
  if (n.present_flags & kNavPresentCurveAhead) {
    const uint8_t* q = p + kCurveAheadOffset;
    CurveAhead& c = n.curve_ahead;
    const size_t point_count = clamp_curve_count(q[0]);  // 上面已拦 >8；循环上界再钳一次做双保险
    c.point_count = uint8_t(point_count);
    c.max_lateral_acceleration_dm_per_s2 = q[1];
    c.horizon_distance_m = get_u16(q + 2);
    c.age_ms = get_u16(q + 4);
    c.curve_flags = q[6];
    for (size_t i = 0; i < point_count; i++) {
      const uint8_t* pt = q + kCurvePointsOffset + kCurvePointSize * i;
      c.points[i].latitude_deg_x1e7 = get_i32(pt);
      c.points[i].longitude_deg_x1e7 = get_i32(pt + 4);
      c.points[i].distance_along_route_m = get_u16(pt + 8);
      c.points[i].target_speed_kph = pt[10];
      c.points[i].curvature_per_m_x1e4 = pt[11];
    }
  }
  if (n.present_flags & kNavPresentNavDebug) {
    const uint8_t* q = p + kNavDebugOffset;
    n.nav_debug.render_duration_us = get_u16(q);
    n.nav_debug.map_match_duration_us = get_u16(q + 2);
    n.nav_debug.route_compute_duration_ms = get_u16(q + 4);
    n.nav_debug.nav_cpu_usage_percent = q[6];
  }
}

}  // namespace

size_t pack_reply(const Reply& r, uint8_t* out, size_t cap) {
  if (cap < kReplyWireSize) return 0;
  std::memset(out, 0, kReplyWireSize);
  put_msg_hdr(out, kTypeReply, r.flags, r.frame_idx, uint32_t(kReplyPayloadSize));
  put_u64(out + 16, r.t_eof);
  for (size_t i = 0; i < kReplyOutputsCount; i++) put_f32(out + kReplyHdrSize + 4 * i, r.outputs[i]);
  for (size_t i = 0; i < kReplyTelemetryCount; i++)
    put_u32(out + kReplyHdrSize + kReplyOutputsSize + 4 * i, r.telemetry[i]);
  pack_nav_ext(r.nav_ext, out + kNavExtOffset);
  // 尾部保留 16 B 置零
  return kReplyWireSize;
}

Err parse_reply(const uint8_t* data, size_t size, Reply* out) {
  // 版本先于长度：旧 v2 的 8320 B REPLY 在这里报 kBadVersion 而不是 kTruncated
  if (size < kMsgHdrSize) return Err::kTruncated;
  uint16_t flags;
  uint32_t frame_idx, len;
  Err err = parse_msg_hdr_typed(data, kTypeReply, &flags, &frame_idx, &len);
  if (err != Err::kOk) return err;
  if (len != kReplyPayloadSize) return Err::kLenMismatch;
  if (size < kReplyWireSize) return Err::kTruncated;
  if (size != kReplyWireSize) return Err::kLenMismatch;

  Reply r;
  r.flags = flags;
  r.frame_idx = frame_idx;
  r.t_eof = get_u64(data + 16);
  for (size_t i = 0; i < kReplyOutputsCount; i++) r.outputs[i] = get_f32(data + kReplyHdrSize + 4 * i);
  for (size_t i = 0; i < kReplyTelemetryCount; i++)
    r.telemetry[i] = get_u32(data + kReplyHdrSize + kReplyOutputsSize + 4 * i);
  parse_nav_ext(data + kNavExtOffset, &r.nav_ext);
  if (out) *out = r;
  return Err::kOk;
}

size_t pack_err(const ErrMsg& e, uint8_t* out, size_t cap) {
  if (cap < kErrWireSize) return 0;
  std::memset(out, 0, kErrWireSize);
  put_msg_hdr(out, kTypeErr, 0, e.frame_idx, uint32_t(kErrPayloadSize));
  put_u32(out + kMsgHdrSize, e.code);
  put_u32(out + kMsgHdrSize + 4, e.detail);
  // 尾部保留 16 B 置零
  return kErrWireSize;
}

Err parse_err(const uint8_t* data, size_t size, ErrMsg* out) {
  if (size < kMsgHdrSize) return Err::kTruncated;
  uint16_t flags;
  uint32_t frame_idx, len;
  Err err = parse_msg_hdr_typed(data, kTypeErr, &flags, &frame_idx, &len);
  if (err != Err::kOk) return err;
  if (size < kErrWireSize) return Err::kTruncated;
  if (len != kErrPayloadSize || size != kErrWireSize) return Err::kLenMismatch;

  ErrMsg e;
  e.code = get_u32(data + kMsgHdrSize);
  e.detail = get_u32(data + kMsgHdrSize + 4);
  e.frame_idx = frame_idx;
  if (out) *out = e;
  return Err::kOk;
}

size_t pack_hello(const Hello& h, uint8_t* out, size_t cap) {
  if (cap < kHelloWireSize) return 0;
  std::memset(out, 0, kHelloWireSize);
  put_msg_hdr(out, kTypeHello, 0, 0, uint32_t(kHelloPayloadSize));
  put_u64(out + kMsgHdrSize, h.instance_id);
  put_u32(out + kMsgHdrSize + 8, h.max_frame);
  // 保留 u32 与尾部保留 16 B 置零
  return kHelloWireSize;
}

Err parse_hello(const uint8_t* data, size_t size, Hello* out) {
  if (size < kMsgHdrSize) return Err::kTruncated;
  uint16_t flags;
  uint32_t frame_idx, len;
  Err err = parse_msg_hdr_typed(data, kTypeHello, &flags, &frame_idx, &len);
  if (err != Err::kOk) return err;
  if (size < kHelloWireSize) return Err::kTruncated;
  if (len != kHelloPayloadSize || size != kHelloWireSize) return Err::kLenMismatch;

  Hello h;
  h.instance_id = get_u64(data + kMsgHdrSize);
  h.max_frame = get_u32(data + kMsgHdrSize + 8);
  if (out) *out = h;
  return Err::kOk;
}

}  // namespace bgm1
