#pragma once

// NavExt（REPLY 8304，160 B）→ 车机侧输出。纯逻辑、无 cereal / Params 依赖，宿主单测直接编译：
//   1. NavStateSnapshot：REPLY → navStateSP 的字段快照（main.cc 再逐字段写 capnp）。
//      CURVE_AHEAD 只取 count / 横向加速度上限 / 前视距离 / age_ms / curve_flags 与每点的
//      距离 / 目标速度 / 曲率，**不读点经纬度**（决策 17：rlog 与 UI 不含任何经纬度）。
//   2. NavLinkCache：最近一条 REPLY 的 NavExt 摘要；断连必须 reset()，保证重连后第一条 REPLY
//      到达前按「无数据」处理（S2：旧弯道点不能被写回）。
//   3. MapTargetWriter：手机位置健康 + CURVE 沿路线距离 → 带本机期限的 Params envelope。
//      失效/删点/降速立即清空或更新，其余变化 ≤2 Hz，不变 1 Hz 刷新。
//   4. clear_map_targets()：退出/上路清理，由 main 注入阻塞 Params put。
//   5. NavLinkSession：reply 线程的链路状态（epoch / HELLO / NavLinkCache / MapTargetWriter）合成一个
//      类，main.cc 的每条断连 / 空闲 / REPLY 路径只调它（AU-1）。
//
// 单位口径：整数编码字段保留线上原值（单位即名字里的单位，无效哨兵原样保留）；定点字段换算成
// SI 浮点；浮点无效哨兵（0xFFFF）用 NaN。navStateSP 字段名与这里一一对应。

#include <cstdint>
#include <functional>
#include <optional>
#include <string>

#include "frame_codec.h"

// REPLY 的 NavExt 快照。has_* 来自 present_flags；四个子结构只在对应 has_* 为真时有意义
// （parse_reply 已把 present=0 的子块读作 0）。
struct NavStateSnapshot {
  uint32_t frame_index = 0;
  uint64_t timestamp_eof_ns = 0;
  bool phone_location_ok = false;
  bool route_ok = false;
  bool nav_ok = false;
  bool rerouting = false;
  bool has_nav_state = false;
  bool has_road_attributes = false;
  bool has_curve_ahead = false;
  bool has_nav_debug = false;

  struct NavStateFields {
    uint8_t status = 0;
    uint8_t maneuver_type = 0;
    uint8_t next_maneuver_type = 0;
    uint8_t roundabout_exit_index = 0;
    uint32_t distance_to_maneuver_m = 0;   // 0xFFFFFFFF 无效
    uint32_t route_remaining_m = 0;        // 0xFFFFFFFF 无效
    uint32_t route_remaining_seconds = 0;  // 0xFFFFFFFF 无效
    uint16_t age_ms = 0;                   // 0xFFFF 无效
    bool nav_map_fed = false;
    bool navigation_fed = false;
    bool on_route = false;
    uint8_t off_route_distance_m = 0;      // 255 无效
    float heading_deg = 0.f;               // NaN = 线上 0xFFFF 无效
  } nav_state;

  struct RoadAttributeFields {
    uint8_t speed_limit_kph = 0;
    uint8_t speed_limit_source = 0;
    uint8_t speed_limit_ahead_kph = 0;
    uint16_t speed_limit_ahead_distance_m = 0;  // 0xFFFF 无效
    uint8_t road_class = 0;
    bool ramp = false;
    bool tunnel = false;
    bool bridge = false;
    bool roundabout = false;
    bool toll = false;
    bool oneway = false;
    bool match_trusted = false;
    uint8_t lane_count = 0;
    float match_distance_m = 0.f;          // NaN = 线上 0xFFFF 未匹配
    uint16_t age_ms = 0;                   // 0xFFFF 无效
  } road_attributes;

  struct CurvePointFields {
    uint16_t distance_along_route_m = 0;   // 0xFFFF 无效
    uint8_t target_speed_kph = 0;          // 0 无
    float curvature_per_m = 0.f;           // 线上 1e-4 /m 换算成 1/m；0 = 直线
  };

  struct CurveAheadFields {
    uint8_t point_count = 0;
    float max_lateral_acceleration_m_per_s2 = 0.f;
    uint16_t horizon_distance_m = 0;
    uint16_t age_ms = 0;                   // 0xFFFF 无效
    bool along_route = false;              // curve_flags bit0：沿路线取
    bool road_lookahead = false;           // bit1：沿当前道路前扫
    bool smoothed = false;                 // bit2：曲率已平滑
    CurvePointFields points[bgm1::kCurveMaxPoints];
  } curve_ahead;

  struct NavDebugFields {
    uint16_t render_duration_us = 0;       // 0xFFFF 无效（饱和 65534）
    uint16_t map_match_duration_us = 0;    // 0xFFFF 无效
    uint16_t route_compute_duration_ms = 0;  // 0xFFFF 无效
    uint8_t nav_cpu_usage_percent = 0;     // 254 饱和，255 无效
  } nav_debug;
};

NavStateSnapshot nav_state_snapshot_from_reply(const bgm1::Reply& reply);

// reply 线程的连接代号跟踪：sender->conn_epoch() 变化（drop 后已重连）时返回 true，
// 调用方据此复位 hello_seen_ / framer / NavLinkCache（R1）。首次调用也返回 true（启动清残留）。
class ConnEpochTracker {
 public:
  bool update(uint32_t epoch) {
    if (!initialized_ || epoch != epoch_) {
      initialized_ = true;
      epoch_ = epoch;
      return true;
    }
    return false;
  }

 private:
  bool initialized_ = false;
  uint32_t epoch_ = 0;
};

// 最近一条 REPLY 的 NavExt 摘要（reply 线程独占）。reset() 后 present=0 / reply_ns=0，
// 重连窗口内的空闲拍不会拿旧数据写回 Params。
class NavLinkCache {
 public:
  // t_eof = REPLY 回显的帧 timestamp_eof（与 nanos_since_boot 同时基，见 main.cc 注释）
  void on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext);
  void reset();
  uint64_t last_reply_ns() const { return last_reply_ns_; }
  uint64_t t_eof() const { return t_eof_; }
  uint8_t present_flags() const { return present_flags_; }
  uint8_t nav_health_flags() const { return nav_health_flags_; }
  const bgm1::CurveAhead& curve() const { return curve_; }
  uint16_t nav_state_age_ms() const { return nav_state_age_ms_; }

 private:
  uint64_t last_reply_ns_ = 0;
  uint64_t t_eof_ = 0;
  uint8_t present_flags_ = 0;
  uint8_t nav_health_flags_ = 0;
  bgm1::CurveAhead curve_;
  uint16_t nav_state_age_ms_ = bgm1::kNavU16Invalid;
};

constexpr uint64_t kNavDataMaxAgeNs = 2'000'000'000;  // 手机采样与距离结果各自的期限
// N1 的 (lat,lon) 同一性匹配容差：50 × 1e-7° ≈ 0.55 m。手机重算/重采样会让同一点有厘米级抖动；
// 若用严格相等，N10 的「点坐标每拍微动」会全部被判成删点，退回 20 Hz 写。
// 注意：这是方框容差，纬度、经度分别比较；经度方向的实际距离是 0.55·cosφ m。
constexpr int32_t kSamePointToleranceX1e7 = 50;

// 手机 NAV_STATE 和 CURVE 的年龄独立判期；时间基准为回显的 C4 t_eof。
struct MapTargetInput {
  bool link_connected = false;
  bool curve_present = false;
  bool nav_state_present = false;
  uint16_t nav_state_age_ms = bgm1::kNavU16Invalid;
  bgm1::CurveAhead curve;
  uint8_t nav_health_flags = 0;  // REPLY 的 nav_health_flags（AU-4：rerouting / route_ok）
  uint64_t last_reply_ns = 0;  // 仅用于事实记录；数据新鲜度按 t_eof − age_ms（N7）
  uint64_t t_eof = 0;          // REPLY 回显的帧 timestamp_eof（ns，同 nanos_since_boot）
  uint64_t now_ns = 0;
};

// 写 MapTargetVelocities 的时机：
//   - 有目标点被删除（含清空）或容差内同一点目标速度下调 → 下一拍写（≤1 个 calib 拍：正常 ≤50 ms，
//     modeld 不发导致 calib 只能按 1 s 超时醒来时最长约 1 s；SCC-map 对旧点有粘滞，N1/N2）。
//   - 其他内容变化（上新点/提速）：≥500 ms（≤2 Hz）。
//   - 内容不变：≥1 s 刷新（有意为之：可自愈被外部改写的值）。
//   - 首次调用必写（清掉上趟进程遗留的弯道点）。
//   - NAV_STATE/CURVE 缺失、phone_location_ok=0、任一年龄未知/过期或断连 → []。
//   - rerouting 或沿路线但 route_ok=0 → []；只接受 0..600 m 单调距离。
//   - 目标坐标只作身份比较，不读取车机位置，也不计算地理距离。
class MapTargetWriter {
 public:
  // 返回 nullopt = 本次不写。
  std::optional<std::string> update(const MapTargetInput& in);

 private:
  static constexpr uint64_t kMinChangedIntervalNs = 500'000'000;  // 2 Hz 上限
  static constexpr uint64_t kRefreshIntervalNs = 1'000'000'000;   // 不变也 1 Hz 刷新

  struct WrittenPoint {
    int32_t latitude_deg_x1e7 = 0;
    int32_t longitude_deg_x1e7 = 0;
    uint8_t target_speed_kph = 0;
    uint16_t distance_along_route_m = 0;
  };

  static bool point_usable(const bgm1::CurvePoint& p);
  static int usable_point_count(const MapTargetInput& in);
  bool same_points(const MapTargetInput& in, int n) const;
  // N1/N2：上次写出的点被删除，或容差内同一点速度下调 → 下一拍写（≤1 个 calib 拍）
  bool urgent_change(const MapTargetInput& in, int n) const;

  bool wrote_ = false;
  uint64_t last_write_ns_ = 0;
  int last_point_count_ = 0;
  WrittenPoint last_points_[bgm1::kCurveMaxPoints];
};

struct NavLinkTick {
  uint64_t now_ns = 0;
};

// AU-1：reply 线程的 NavExt 链路状态（epoch 跟踪 + NavExt 摘要 + 弯道目标 writer + HELLO 标志）。
// main.cc 的 fail / epoch 变化 / 未连接 / 空闲拍 / REPLY / ERR 只调这里的方法，再把 take_pending()
// 交给 calib 线程写 Params；宿主单测驱动同一个类，不再手抄 main 的接线。
// 每个方法内部决定「复位 + 以 link=false 走 writer」，保证 fail/ERR/换连接/未连接都立即得到 "[]"。
// reply 线程独占，无锁。
class NavLinkSession {
 public:
  // epoch 变化（首次调用也算）→ 复位并返回 true；调用方据此 framer.clear()。
  // make_tick 仅在 epoch 变化时取当前车机 BOOTTIME。
  template <class MakeTick>
  bool on_epoch(uint32_t epoch, MakeTick&& make_tick) {
    if (!epoch_.update(epoch)) return false;
    on_disconnect(make_tick());
    return true;
  }
  // fail / ERR / 未连接：HELLO 标志、NavExt 摘要全部复位，并以 link=false 走 writer。
  void on_disconnect(const NavLinkTick& tick);
  void on_hello_ok() { hello_seen_ = true; }
  bool hello_seen() const { return hello_seen_; }
  // 成功解析一条 REPLY：reply_ns 为到达时刻，t_eof 为 REPLY 回显的帧时刻。
  void on_reply(uint64_t reply_ns, uint64_t t_eof, const bgm1::NavExt& ext, const NavLinkTick& tick);
  // 没有新 REPLY 的拍：手机位置 / CURVE 过期时清掉旧弯道点。
  void on_idle(bool connected, const NavLinkTick& tick);
  // 取走待写的 MapTargetVelocities（只有最后一次决策有意义，取走即空）。
  std::optional<std::string> take_pending() {
    std::optional<std::string> out;
    out.swap(pending_);
    return out;
  }

 private:
  void decide(bool link_connected, const NavLinkTick& tick);

  ConnEpochTracker epoch_;
  NavLinkCache cache_;
  MapTargetWriter writer_;
  bool hello_seen_ = false;
  std::optional<std::string> pending_;
};

// 退出清理 / 上路清理的 Params 操作（main 注入：mem_params.put，阻塞调用）。
struct MapParamsOps {
  std::function<void(const char*, const std::string&)> put;
};
// MapTargetVelocities = "[]"。
void clear_map_targets(const MapParamsOps& ops);
