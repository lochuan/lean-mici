// BGM1 线协议（v3）编解码 —— C4 上行进程（sunnypilot bigmodeld）与 App 服务端共用同一份实现，
// 两端 frame_codec.{h,cpp} 必须逐字节同源。本注释即权威布局（设计文档：协议 v3 定稿）。
// v3 不兼容 v2：线上 kVersion 锁步 2→3，一端版本不符即断连（见「版本」）。全小端。
//
// ===== 版本 =====
//   MsgHdr.ver 严格等于 kVersion(3)；不符 → kBadVersion。parse_reply 先验 MsgHdr 再验长度：旧 v2 的
//   8320 B REPLY（len 8280）在 parse_reply 里报 kBadVersion 而非截断；FRAME 的 ver 在头部前 16 B，
//   parse_frame_header 先验它。
//   实际路径：手机收到版本不符的 FRAME 回 ERR VERSION_MISMATCH（code 1）并断连。C4 侧版本不符
//   由 HELLO 判定——手机连上先发 HELLO，C4 的 parse_hello 得 kBadVersion 即版本不符（日志带
//   got/want，断连重连）；v2 手机的 REPLY 因 len≠8440 在 C4 分帧处就以「协议头非法」断连，
//   到不了 parse_reply。真版本不一致时对端 ERR 的 ver 也不同，C4 的 parse_err 同样得 kBadVersion，
//   所以 C4 不依赖 ERR.detail 判版本。ERR.detail = version_mismatch_detail(对端 ver, 本端 ver)
//   = 对端 ver << 8 | 本端 ver（例：对端 2、本端 3 → 0x0203），供人读日志。
//
// ===== 字段语义通则 =====
//   - 无效值：字段「没有」时填各自的无效值（见各表）；规格保留位 pack 强制写 0。
//   - 子块「其余字段无意义」：NavExt 的 present_flags 位为 0 的子块全 0（缺省）。GNSS has_fix=0 时
//     位置类字段（latitude_deg_x1e7、longitude_deg_x1e7、bearing_centideg、horizontal_accuracy_dm、
//     bearing_valid，以及 VEHICLE.bearing_accuracy_half_deg → 255）无意义；speed_cm_per_s + speed_valid
//     （deviceMotion 车体系前向速度，与定位无关）与 age_ms（上次 fix 的年龄，饱和 4095）保留，隧道航位
//     推算靠它们（有 yaw 与 speed 即可推算）。车机完全没有数据时整块 16 B 为 0。
//     pack 写规范形，parse 读作缺省，与调用方结构体里的值无关。
//   - 带 valid 位的字段（speed_cm_per_s/speed_valid、bearing_centideg/bearing_valid）以 valid 位为准：
//     无论 has_fix 取何值，valid=0 ⇒ 字段写 0（pack 与 parse 两侧都规范化），消费者看 valid 位；
//     只有没有 valid 位的字段（如 horizontal_accuracy_dm）才用 0xFFFF 哨兵表示缺失。
//   - age_ms 只在 has_fix=1 或 speed_valid=1 时有意义；完全无数据时整块为 0，此时 age_ms=0 没有含义。
//   - VEHICLE：yaw_rate_centideg_per_s == INT16_MIN ⇔ yaw_valid=0；pack 保证两者一致。
//
// ===== FRAME（type 0x10）=====
//     0   16  MsgHdr：magic "BGM1"、ver=3、type=0x10、flags（bit0=road IDR、bit1=wide IDR）、
//                frame_idx u32、len u32 = road 段字节数
//    16    8  t_eof u64（timestamp_eof，ns）
//    24   32  desire f32[8]（本帧 pulse，全零=无事件）
//    56    8  traffic_convention f32[2]
//    64    8  action_t f32[2]（[lat, long] 秒）
//    72   36  warp_road f32[9]（3×3 透视矩阵，行主序；C4 已应用的矩阵，仅遥测/dump，App 不再 warp）
//   108   36  warp_wide f32[9]
//   144   16  GNSS（见下表）
//   160    8  VEHICLE（见下表）
//   168   ..  road 段（road_len = MsgHdr.len，HEVC Annex-B 码流，warp 后 512×256 NV12 编码）
//    ..    4  wide_len u32
//    ..   ..  wide 段（HEVC Annex-B 码流）
//    ..   16  尾部保留 16 B（恒填零；手机把非零当 wide_len 错位探针 → len_mismatch）
//   线长 = 168 + road_len + 4 + wide_len + 16。
//
//   GNSS 16 B（偏移 144；WGS-84，不做 GCJ-02；has_fix=0 ⇒ 无定位，反之不成立：见通则）：
//     144  i32  latitude_deg_x1e7          度 × 1e7（以 has_fix 判有效；has_fix=0 时写 0）
//     148  i32  longitude_deg_x1e7         度 × 1e7
//     152  u16  speed_cm_per_s             cm/s（deviceMotion.velocityDevice 车体系前向分量，与定位无关：
//                                          has_fix=0 时只要 speed_valid 仍有效）；speed_valid=0 ⇒ 写 0
//     154  u16  bearing_centideg           0.01°，正北顺时针；bearing_valid=0 ⇒ 写 0
//     156  u16  horizontal_accuracy_dm     水平精度 0.1 m；0xFFFF = 未知，饱和 0xFFFE
//     158  u16  fix_age_and_valid_flags    bit0–11 age_ms（fix 到达 C4 → 本帧 t_eof，饱和 4095）·
//                                          bit12 has_fix（hasFix ∧ age ≤ 2000 ms）· bit13 保留 ·
//                                          bit14 speed_valid · bit15 bearing_valid
//   VEHICLE 8 B（偏移 160）：
//     160  i16  yaw_rate_centideg_per_s    0.01 °/s，右转（顺时针）为正
//                                          （deviceMotion.angularVelocityDevice 车体系 z 分量）；INT16_MIN = 无效
//     162  u8   vehicle_state_flags        bit0 yaw_valid · bit1 reverse（倒车中）· bit2 standstill ·
//                                          bit3–7 保留 0
//     163  u8   bearing_accuracy_half_deg  航向精度 0.5°（1σ，gpsLocationExternal.bearingAccuracyDeg）；
//                                          255 = 未知 / 无 fix，饱和 254
//     164   4   保留（pack 置 0，解析端忽略）
//
// ===== REPLY（type 0x11）=====
//     0   16  MsgHdr：magic "BGM1"、ver=3、type=0x11、flags（bit0=SEQ_RESET、bit1=ZERO_PAIR）、
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
//                parse 读作 0）。nav_health_flags：bit0 gps_ok · bit1 route_ok · bit2 nav_ok ·
//                bit3 rerouting（手机 :nav 按判据算，C4 只读位）
//     8308  24  NAV_STATE
//     8332  16  ROAD_ATTR
//     8348 104  CURVE_AHEAD（头 8 B + 8 个采样点 × 12 B）
//     8452   8  NAV_DEBUG
//     8460   4  保留（pack 置 0，解析端忽略）
//   present_flags / nav_health_flags 高 4 位、nav_input_flags bit4–7、road_edge_flags bit6、curve_flags bit3–7 等规格保留位 pack 强制写 0。
//   各子结构带 age_ms u16（数据最后更新 → 本帧收齐，饱和 0xFFFE，0xFFFF 未知）；有效时刻 ≈ t_eof − age_ms。
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
constexpr uint8_t kVersion = 3;  // v3：FRAME 头 +GNSS/VEHICLE、REPLY +NavExt；v2 = 已 warp 512×256 HEVC，v1 = 相机全幅
constexpr uint8_t kTypeFrame = 0x10;
constexpr uint8_t kTypeReply = 0x11;
constexpr uint8_t kTypeErr = 0x7F;
constexpr uint8_t kTypeHello = 0x01;

constexpr size_t kMsgHdrSize = 16;
constexpr size_t kFrameHdrSize = 168;  // MsgHdr 16 + 扩展 152（元数据 128 + GNSS 16 + VEHICLE 8）
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

// ---- GNSS（FRAME 偏移 144，16 B）/ VEHICLE（偏移 160，8 B）----

constexpr uint16_t kGnssAgeMask = 0x0FFF;      // fix_age_and_valid_flags bit0–11：age_ms
constexpr uint16_t kGnssAgeMax = 4095;         // age_ms 饱和值
constexpr uint16_t kGnssHasFix = 1u << 12;
constexpr uint16_t kGnssSpeedValid = 1u << 14;
constexpr uint16_t kGnssBearingValid = 1u << 15;
constexpr uint16_t kGnssHorizontalAccuracyUnknownDm = 0xFFFF;      // horizontal_accuracy_dm 的未知哨兵（speed/bearing 以 valid 位为准，无效写 0）
constexpr uint16_t kGnssHorizontalAccuracyMaxDm = 0xFFFE;      // horizontal_accuracy_dm 饱和值

// 组 fix_age_and_valid_flags：age_ms 饱和到 4095，三个有效位按实参置位
constexpr uint16_t make_fix_age_and_valid_flags(uint32_t age_ms, bool has_fix, bool speed_valid,
                                                bool bearing_valid) {
  return uint16_t((age_ms > kGnssAgeMax ? kGnssAgeMax : age_ms) | (has_fix ? kGnssHasFix : 0) |
                  (speed_valid ? kGnssSpeedValid : 0) | (bearing_valid ? kGnssBearingValid : 0));
}

// 缺省全 0：has_fix=0 ⇒ 无定位（反之不成立），这是生产者完全无数据时的取值
struct Gnss {
  int32_t latitude_deg_x1e7 = 0;
  int32_t longitude_deg_x1e7 = 0;
  uint16_t speed_cm_per_s = 0;
  uint16_t bearing_centideg = 0;
  uint16_t horizontal_accuracy_dm = 0;
  uint16_t fix_age_and_valid_flags = 0;

  bool has_fix() const { return fix_age_and_valid_flags & kGnssHasFix; }
  bool speed_valid() const { return fix_age_and_valid_flags & kGnssSpeedValid; }
  bool bearing_valid() const { return fix_age_and_valid_flags & kGnssBearingValid; }
  uint16_t age_ms() const { return fix_age_and_valid_flags & kGnssAgeMask; }
};

constexpr int16_t kYawRateInvalidCentidegPerS = -32768;  // INT16_MIN
constexpr uint8_t kVehicleYawValid = 1u << 0;
constexpr uint8_t kVehicleReverse = 1u << 1;
constexpr uint8_t kVehicleStandstill = 1u << 2;
constexpr uint8_t kGnssBearingAccuracyUnknownHalfDeg = 255;  // 未知 / 无 fix
constexpr uint8_t kGnssBearingAccuracyMaxHalfDeg = 254;      // 饱和值（≥ 127°）

// 缺省 = 无数据（yaw 无效、航向精度未知）；VEHICLE 保留 4 B 不入结构，pack 置 0、parse 忽略
struct Vehicle {
  int16_t yaw_rate_centideg_per_s = kYawRateInvalidCentidegPerS;
  uint8_t vehicle_state_flags = 0;
  uint8_t bearing_accuracy_half_deg = kGnssBearingAccuracyUnknownHalfDeg;

  bool yaw_valid() const { return vehicle_state_flags & kVehicleYawValid; }
  bool reversing() const { return vehicle_state_flags & kVehicleReverse; }
  bool standstill() const { return vehicle_state_flags & kVehicleStandstill; }
};

// ERR VERSION_MISMATCH 的 detail：对端 ver << 8 | 本端 ver
constexpr uint32_t version_mismatch_detail(uint8_t peer_ver, uint8_t local_ver) {
  return (uint32_t(peer_ver) << 8) | local_ver;
}

// FRAME 头（168 B）+ wide 长度。wide_len 不在 168 B 头内，序列化在 road 段之后。
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
  Gnss gnss;
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

// 组帧头（168 B，小端）
void pack_frame_header(const FrameHeader& hdr, uint8_t out[kFrameHdrSize]);

// 解析 MsgHdr（16 B），校验 magic/ver/type
Err parse_msg_hdr(const uint8_t in[kMsgHdrSize], uint8_t* type, uint16_t* flags,
                  uint32_t* frame_idx, uint32_t* len);

// 解析 FRAME 头（168 B，含 MsgHdr；不校验载荷长度）
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

// ---- NavExt（REPLY 偏移 8304，160 B）：全部子结构缺省为 0，缺省 Reply = 「无导航数据」----

constexpr uint8_t kNavPresentNavState = 1u << 0;
constexpr uint8_t kNavPresentRoadAttr = 1u << 1;
constexpr uint8_t kNavPresentCurveAhead = 1u << 2;
constexpr uint8_t kNavPresentNavDebug = 1u << 3;

constexpr uint8_t kNavHealthGpsOk = 1u << 0;
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
