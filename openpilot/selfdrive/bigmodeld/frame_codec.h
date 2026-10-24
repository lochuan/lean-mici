// BGM1 v4，全部小端；与车机 frame_codec.h/.cpp 保持逐字节相同。
// v3 不兼容：先验证前 16 B 的版本，再解释 payload。
// FRAME: MsgHdr@0/16, t_eof@16/8, desire@24/32, traffic@56/8, action_t@64/8,
// warp_road@72/36, warp_wide@108/36, Vehicle@144/8, road@152。
// Vehicle: speed_cm_per_s u16@144, yaw i16@146 (INT16_MIN invalid), flags u8@148,
// flags: bit0 yaw_valid, bit1 reverse, bit2 standstill, bit3 speed_valid; reserved@149/3=0。
// speed_valid=0 时 speed/reverse/standstill 清零；yaw 独立有效。FRAME 不含位置观测。
// 线长 = 172 + road_len + wide_len；相机 t_eof 不属于手机定位时钟域。
// NAV_STATE.age_ms: phone_location_ok 时为手机采样年龄，正年龄向上取整到 ms；
// 不可用时为状态发布年龄。CURVE.age_ms 仍是计算结果年龄，各自判期。
// ===== REPLY（type 0x11）=====
//     0   16  MsgHdr：magic "BGM1"、ver=4、type=0x11、flags（bit0=SEQ_RESET、bit1=ZERO_PAIR）、
//                frame_idx u32（回显）、len u32 = 8440（payload 字节数，不含 t_eof）
//    16    8  t_eof u64（回显）
//    24 8264  outputs[0:2066) f32
//  8288   16  遥测 u32×4：srv_recv_us、srv_prep_us、srv_htp_us、srv_total_us
//  8304  160  NavExt（定长导航扩展区，每条 REPLY 全带）
//  8464   16  尾部保留 16 B（恒填零，不校验）
//   线长 = 24 + 8440 + 16 = 8480。
//
//   NavExt 160 B（偏移相对 REPLY 起点）：
//     8304   4  健康头：present_flags u8 · nav_health_flags u8 · 保留 u16
//                present_flags：bit0 NAV_STATE · bit1 ROAD_ATTR · bit2 CURVE_AHEAD · bit3 NAV_DEBUG
//                各「有提供者」；位为 0 ⇒ 该子结构全 0，C4 当「无此功能」（pack 强制写 0，
//                parse 读作 0）。nav_health_flags：bit0 phone_location_ok · bit1 route_ok · bit2 nav_ok ·
//                bit3 rerouting（手机 :nav 按判据算，C4 只读位）
//     8308  24  NAV_STATE
//     8332  16  ROAD_ATTR
//     8348 104  CURVE_AHEAD（头 8 B + 8 个采样点 × 12 B）
//     8452   8  NAV_DEBUG
//     8460   4  保留（pack 置 0，解析端忽略）
//   present_flags / nav_health_flags 高 4 位、nav_input_flags bit4–7、road_edge_flags bit6、curve_flags bit3–7 等规格保留位 pack 强制写 0。
//   age_ms u16：到本帧收齐的年龄，正年龄向上取整到 ms，饱和 0xFFFE，0xFFFF 未知。
//   phone_location_ok=1 时 NAV_STATE.age_ms 来自手机采样，motion tick 不续期；不可用时
//   按状态发布计龄且清 bit0。CURVE.age_ms 独立来自距离结果；有效时刻保守估计 t_eof−age_ms。
//
//   NAV_STATE（8308，24 B）：
//     +0   u8   nav_status                0 未运行 · 1 空闲 · 2 导航中 · 3 偏航重算 · 4 定位丢失 · 5 到达 · 6 地图数据缺 · 7 故障
//     +1   u8   maneuver_type             Valhalla maneuver type 0–43；255 无效
//     +2   u8   next_maneuver_type        下下个机动；255 无效
//     +3   u8   roundabout_exit_index     环岛出口序号；0 不适用
//     +4   u32  distance_to_maneuver_m    m；0xFFFFFFFF 无效
//     +8   u32  route_remaining_m         m；0xFFFFFFFF 无效
//     +12  u32  route_remaining_s         s；0xFFFFFFFF 无效
//     +16  u16  age_ms                    0xFFFF 无效
//     +18  u8   nav_input_flags           bit0 nav_map 已喂模型 · bit1 navigation 已喂模型 · bit2 已吸附路线 ·
//                                         bit3 路线来自离线 Valhalla · bit4–7 保留
//     +19  u8   off_route_distance_m      m，饱和 254；255 无效
//     +20  u16  heading_centideg          吸附后路线航向 0.01°；0xFFFF 无效
//     +22  u16  保留
//   ROAD_ATTR（8332，16 B）：
//     +0   u8   speed_limit_kph              标牌限速 km/h；0 无数据；254 无限速
//     +1   u8   speed_limit_source           0 无 · 1 OSM 显式 · 2 隐含/默认（预留）· 3 其他（预留）
//     +2   u8   speed_limit_ahead_kph        前方下一段限速；0 无
//     +3   u8   road_class                   Valhalla road_class 0 motorway … 7 service_other；255 无效
//     +4   u16  speed_limit_ahead_distance_m m；0xFFFF 无效
//     +6   u16  age_ms                       0xFFFF 无效
//     +8   u8   road_edge_flags              bit0 匝道 · bit1 隧道 · bit2 桥 · bit3 环岛 · bit4 收费 · bit5 单行 ·
//                                            bit6 保留 · bit7 匹配可信
//     +9   u8   lane_count                   0 未知
//     +10  u16  保留（原坡度位，坡度不进协议）
//     +12  u16  match_distance_dm            匹配点到 GNSS 点距离 0.1 m；0xFFFF 未匹配
//     +14  u16  保留
//   CURVE_AHEAD（8348，104 B）：
//     +0  u8   point_count      有效采样点数 0–8。pack 把 >8 钳到 8；parse 把线上 >8 当坏块：
//                               清 present_flags bit2，子块整块读作缺省 0（与 present_flags=0 同：其余字段无意义），
//                               因为这些点要喂纵向
//     +1  u8   max_lateral_acceleration_dm_per_s2  手机算目标速度用的横向加速度上限 0.1 m/s²（已定 20）；C4 不复算
//     +2  u16  horizon_distance_m        采样覆盖的前方距离 m（已定 600）
//     +4  u16  age_ms
//     +6  u8   curve_flags      bit0 沿路线取 · bit1 沿当前道路前扫 · bit2 曲率已平滑
//     +7  u8   保留
//     +8  8 × 点（每点 12 B；point_count 之外的点 pack 为 0）：
//           +0 i32 latitude_deg_x1e7 · +4 i32 longitude_deg_x1e7 · +8 u16 distance_along_route_m（0xFFFF 无效）·
//           +10 u8 target_speed_kph（= min(254, 3.6·√(max_lateral_acceleration/κ))，0 无）· +11 u8 curvature_per_m_x1e4（值 = κ(1/m) × 1e4，0 直线）
//   NAV_DEBUG（8452，8 B）：
//     +0 u16 render_duration_us（饱和 65534，0xFFFF 无效）· +2 u16 map_match_duration_us（0xFFFF 无效）· +4 u16 route_compute_duration_ms（0xFFFF 无效）·
//     +6 u8 nav_cpu_usage_percent（饱和 254，255 无效）· +7 u8 保留
//
// ===== ERR（type 0x7F）=====
//   MsgHdr（len=8）‖ code u32 ‖ detail u32 ‖ 尾部保留 16 B（零），线长 40。
//   code 数值：1 VERSION_MISMATCH、2 NOT_PAIRED、3 PAIR_MISMATCH、4 BAD_FRAME、5 INTERNAL。
//   detail：VERSION_MISMATCH = version_mismatch_detail(对端 ver, 本端 ver)；其余 = 出错帧的 frame_idx。
//
// ===== HELLO（type 0x01）=====
//   见文件后部：MsgHdr（len=16）‖ instance_id u64 ‖ max_frame u32 ‖ 保留 u32 ‖ 尾部 16 B，线长 48。
//
// 无 Android 依赖，可原样带去 sunnypilot 仓库；宿主单测见 app/tests/test_frame_codec.cpp，
// Python 镜像实现见 app/tools/bgm1_frame.py，两端对同一 golden 字节样例互证。
#pragma once

#include <cstddef>
#include <cstdint>

namespace bgm1 {

constexpr uint32_t kMagic = 0x314D4742u;  // "BGM1" 小端
constexpr uint8_t kVersion = 4;  // motion-only FRAME
constexpr uint8_t kTypeFrame = 0x10;
constexpr uint8_t kTypeReply = 0x11;
constexpr uint8_t kTypeErr = 0x7F;
constexpr uint8_t kTypeHello = 0x01;

constexpr size_t kMsgHdrSize = 16;
constexpr size_t kFrameHdrSize = 152;  // MsgHdr 16 + metadata 128 + Vehicle 8
constexpr size_t kWideLenSize = 4;
constexpr size_t kTailReservedSize = 16;  // 各消息尾部保留 16 B（原 MAC 位，HMAC 已随 ADR-0003 撤销，恒零）
constexpr size_t kFrameMinSize = kFrameHdrSize + kWideLenSize + kTailReservedSize;

constexpr uint16_t kFlagRoadIdr = 1u << 0;
constexpr uint16_t kFlagWideIdr = 1u << 1;

// 小端读 u32（streaming 消费方读 wide_len 等散布字段用）
inline uint32_t read_u32_le(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

// 每段码流上限（HELLO.max_frame 同义），超过即长度错。10 Mb/s/路实测 I 帧峰值 ~225 KB。
constexpr uint32_t kDefaultMaxSegment = 1u << 20;

enum class Err {
  kOk = 0,
  kBadMagic,
  kBadVersion,
  kBadType,
  kTruncated,     // 字节数不足以容纳头/载荷
  kLenTooLarge,   // road/wide 段超上限
  kLenMismatch,   // 线长与 road_len/wide_len 声明不符
};

constexpr int16_t kYawRateInvalidCentidegPerS = -32768;  // INT16_MIN
constexpr uint8_t kVehicleYawValid = 1u << 0;
constexpr uint8_t kVehicleReverse = 1u << 1;
constexpr uint8_t kVehicleStandstill = 1u << 2;
constexpr uint8_t kVehicleSpeedValid = 1u << 3;

struct Vehicle {
  int16_t yaw_rate_centideg_per_s = kYawRateInvalidCentidegPerS;
  uint8_t vehicle_state_flags = 0;
  uint16_t speed_cm_per_s = 0;

  bool speed_valid() const { return vehicle_state_flags & kVehicleSpeedValid; }

  bool yaw_valid() const { return vehicle_state_flags & kVehicleYawValid; }
  bool reversing() const { return vehicle_state_flags & kVehicleReverse; }
  bool standstill() const { return vehicle_state_flags & kVehicleStandstill; }
};

// ERR VERSION_MISMATCH 的 detail：对端 ver << 8 | 本端 ver
constexpr uint32_t version_mismatch_detail(uint8_t peer_ver, uint8_t local_ver) {
  return (uint32_t(peer_ver) << 8) | local_ver;
}

// FRAME 头（152 B）+ wide 长度。wide_len 不在 152 B 头内，序列化在 road 段之后。
struct FrameHeader {
  uint16_t flags = 0;
  uint32_t frame_idx = 0;
  uint32_t road_len = 0;
  uint32_t wide_len = 0;
  uint64_t t_eof = 0;
  float desire[8] = {0};
  float traffic_convention[2] = {0};
  float action_t[2] = {0};
  float warp_road[9] = {0};
  float warp_wide[9] = {0};
  Vehicle vehicle;

  bool road_idr() const { return flags & kFlagRoadIdr; }
  bool wide_idr() const { return flags & kFlagWideIdr; }
};

// 整条 FRAME 的拆帧视图：road/wide 指针指向被解析缓冲内部（不拷贝）。
struct FrameView {
  FrameHeader hdr;
  const uint8_t* road = nullptr;
  const uint8_t* wide = nullptr;
};

// 组帧头（152 B，小端）
void pack_frame_header(const FrameHeader& hdr, uint8_t out[kFrameHdrSize]);

// 解析 MsgHdr（16 B），校验 magic/ver/type
Err parse_msg_hdr(const uint8_t in[kMsgHdrSize], uint8_t* type, uint16_t* flags,
                  uint32_t* frame_idx, uint32_t* len);

// 解析 FRAME 头（152 B，含 MsgHdr；不校验载荷长度）
Err parse_frame_header(const uint8_t in[kFrameHdrSize], FrameHeader* out);

// FRAME 线长
inline size_t frame_wire_size(uint32_t road_len, uint32_t wide_len) {
  return kFrameHdrSize + road_len + kWideLenSize + wide_len + kTailReservedSize;
}

// 组完整 FRAME：hdr ‖ road ‖ wide_len ‖ wide ‖ 尾部保留 16 B（零）。返回写入字节数，
// cap 不足返回 0（写入不超过 cap 的部分）。hdr 的 road_len/wide_len 与实参缓冲必须一致。
size_t pack_frame(const FrameHeader& hdr, const uint8_t* road, const uint8_t* wide,
                  uint8_t* out, size_t cap);

// 解析完整 FRAME（含长度校验；尾部保留 16 B 不在此校验）。size 不含在缓冲之外的任何字节。
Err parse_frame(const uint8_t* data, size_t size, FrameView* out,
                uint32_t max_segment = kDefaultMaxSegment);

// ---- REPLY（type 0x11，09 号 §5）----

constexpr uint16_t kFlagSeqReset = 1u << 0;  // REPLY flags bit0：本帧服务端新建序列
constexpr uint16_t kFlagZeroPair = 1u << 1;  // REPLY flags bit1：t-4 用了零图

constexpr size_t kReplyHdrSize = 24;  // MsgHdr 16 + t_eof 回显 8
constexpr size_t kReplyOutputsCount = 2066;
constexpr size_t kReplyOutputsSize = kReplyOutputsCount * 4;
constexpr size_t kReplyTelemetryCount = 4;  // recv/prep/htp/total us
constexpr size_t kReplyNavExtSize = 160;
constexpr size_t kReplyPayloadSize =
    kReplyOutputsSize + kReplyTelemetryCount * 4 + kReplyNavExtSize;              // 8440
constexpr size_t kReplyWireSize = kReplyHdrSize + kReplyPayloadSize + kTailReservedSize;  // 8480

// NavExt 在 REPLY 里的起始偏移（头部 ‖ outputs ‖ 遥测之后）
constexpr size_t kNavExtOffset = kReplyHdrSize + kReplyOutputsSize + kReplyTelemetryCount * 4;
static_assert(kNavExtOffset == 8304, "NavExt 从 REPLY 偏移 8304 起");

// ---- NavExt（REPLY 偏移 8304，160 B）：全部子结构缺省为 0，缺省 Reply = 「无导航数据」----

constexpr uint8_t kNavPresentNavState = 1u << 0;
constexpr uint8_t kNavPresentRoadAttr = 1u << 1;
constexpr uint8_t kNavPresentCurveAhead = 1u << 2;
constexpr uint8_t kNavPresentNavDebug = 1u << 3;

constexpr uint8_t kNavHealthPhoneLocationOk = 1u << 0;
constexpr uint8_t kNavHealthRouteOk = 1u << 1;
constexpr uint8_t kNavHealthNavOk = 1u << 2;
constexpr uint8_t kNavHealthRerouting = 1u << 3;

// 各子结构的无效值（生产者「该字段没有」时填）
constexpr uint32_t kNavU32Invalid = 0xFFFFFFFFu;
constexpr uint16_t kNavU16Invalid = 0xFFFF;
constexpr uint8_t kNavU8Invalid = 255;

constexpr size_t kCurveMaxPoints = 8;

struct NavState {  // 线上 24 B
  uint8_t nav_status = 0;
  uint8_t maneuver_type = 0;
  uint8_t next_maneuver_type = 0;
  uint8_t roundabout_exit_index = 0;
  uint32_t distance_to_maneuver_m = 0;
  uint32_t route_remaining_m = 0;
  uint32_t route_remaining_s = 0;
  uint16_t age_ms = 0;
  uint8_t nav_input_flags = 0;
  uint8_t off_route_distance_m = 0;
  uint16_t heading_centideg = 0;
};

struct RoadAttr {  // 线上 16 B
  uint8_t speed_limit_kph = 0;
  uint8_t speed_limit_source = 0;
  uint8_t speed_limit_ahead_kph = 0;
  uint8_t road_class = 0;
  uint16_t speed_limit_ahead_distance_m = 0;
  uint16_t age_ms = 0;
  uint8_t road_edge_flags = 0;
  uint8_t lane_count = 0;
  uint16_t match_distance_dm = 0;
};

struct CurvePoint {  // 线上 12 B
  int32_t latitude_deg_x1e7 = 0;
  int32_t longitude_deg_x1e7 = 0;
  uint16_t distance_along_route_m = 0;
  uint8_t target_speed_kph = 0;
  uint8_t curvature_per_m_x1e4 = 0;  // 值 = κ(1/m) × 1e4
};

struct CurveAhead {  // 线上 104 B；pack 把 point_count 钳到 8，线上 point_count>8 ⇒ parse 清 present_flags bit2；point_count 之外的点读写为 0
  uint8_t point_count = 0;
  uint8_t max_lateral_acceleration_dm_per_s2 = 0;
  uint16_t horizon_distance_m = 0;
  uint16_t age_ms = 0;
  uint8_t curve_flags = 0;
  CurvePoint points[kCurveMaxPoints] = {};
};

struct NavDebug {  // 线上 8 B
  uint16_t render_duration_us = 0;
  uint16_t map_match_duration_us = 0;
  uint16_t route_compute_duration_ms = 0;
  uint8_t nav_cpu_usage_percent = 0;
};

// present_flags 位为 0 的子结构：pack 写 0，parse 读作 0（与结构体里的值无关）
struct NavExt {
  uint8_t present_flags = 0;
  uint8_t nav_health_flags = 0;
  NavState nav_state;
  RoadAttr road_attr;
  CurveAhead curve_ahead;
  NavDebug nav_debug;
};

// REPLY 整条消息。outputs/telemetry 解析时拷贝（小端转主机序），组帧时原样写回。
struct Reply {
  uint16_t flags = 0;
  uint32_t frame_idx = 0;  // 回显
  uint64_t t_eof = 0;      // 回显
  float outputs[kReplyOutputsCount] = {};                     // outputs[0:2066)
  uint32_t telemetry[kReplyTelemetryCount] = {};              // 遥测 u32×4
  NavExt nav_ext;                                             // 缺省 present_flags=0 = 无导航数据

  bool seq_reset() const { return flags & kFlagSeqReset; }
  bool zero_pair() const { return flags & kFlagZeroPair; }
};

// 组完整 REPLY：头 24 B ‖ outputs ‖ 遥测 ‖ NavExt 160 B ‖ 尾部保留 16 B（零）。返回写入字节数，cap 不足返回 0。
size_t pack_reply(const Reply& r, uint8_t* out, size_t cap);

// 解析完整 REPLY：先验 MsgHdr（magic/ver/type，版本先于长度），再验 len 与线长。
Err parse_reply(const uint8_t* data, size_t size, Reply* out);

// ---- ERR（type 0x7F，09 号 §2）----

// ERR code 数值定义（09 号只列名字，数值以本表为准，与 bgm1_frame.py 的 ERR_CODES 一致）
constexpr uint32_t kErrVersionMismatch = 1;
constexpr uint32_t kErrNotPaired = 2;
constexpr uint32_t kErrPairMismatch = 3;
constexpr uint32_t kErrBadFrame = 4;
constexpr uint32_t kErrInternal = 5;

constexpr size_t kErrPayloadSize = 8;  // code u32 + detail u32
constexpr size_t kErrWireSize = kMsgHdrSize + kErrPayloadSize + kTailReservedSize;  // 40

struct ErrMsg {
  uint32_t code = kErrBadFrame;
  uint32_t detail = 0;
  uint32_t frame_idx = 0;  // MsgHdr.frame_idx
};

// 组完整 ERR：MsgHdr（len=8）‖ code u32 ‖ detail u32 ‖ 尾部保留 16 B（零）。返回写入字节数，cap 不足返回 0。
size_t pack_err(const ErrMsg& e, uint8_t* out, size_t cap);

// 解析完整 ERR（含长度校验；尾部保留位不校验）。
Err parse_err(const uint8_t* data, size_t size, ErrMsg* out);

// ---- HELLO（type 0x01，06 号）：连接建立后服务端首条消息（无鉴权，ADR-0003）----
// 线上布局：MsgHdr（type=0x01，frame_idx=0，len=16）‖ instance_id u64 ‖ max_frame u32 ‖
// 保留 u32（置 0）‖ 尾部保留 16 B（零），线长 48。instance_id 每次服务端启动随机，C4 用来区分
// 服务端重启与网络闪断；max_frame = 每段码流上限（kDefaultMaxSegment 同义）。

constexpr size_t kHelloPayloadSize = 16;
constexpr size_t kHelloWireSize = kMsgHdrSize + kHelloPayloadSize + kTailReservedSize;  // 48

struct Hello {
  uint64_t instance_id = 0;
  uint32_t max_frame = kDefaultMaxSegment;
};

// 组完整 HELLO：MsgHdr ‖ payload ‖ 尾部保留 16 B（零）。返回写入字节数，cap 不足返回 0。
size_t pack_hello(const Hello& h, uint8_t* out, size_t cap);

// 解析完整 HELLO（含长度校验；保留 u32 解析端忽略）。
Err parse_hello(const uint8_t* data, size_t size, Hello* out);

}  // namespace bgm1
