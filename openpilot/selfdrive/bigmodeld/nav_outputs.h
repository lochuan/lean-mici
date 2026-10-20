#pragma once

// NavExt（REPLY 8304，160 B）→ 车机侧输出。纯逻辑、无 cereal / Params 依赖，宿主单测直接编译：
//   1. NavStateSnapshot：REPLY → navStateSP 的字段快照（main.cc 再逐字段写 capnp）。
//      CURVE_AHEAD 只取 count / 横向加速度上限 / 前视距离 / age_ms / curve_flags 与每点的
//      距离 / 目标速度 / 曲率，**不读点经纬度**（决策 17：rlog 与 UI 不含任何经纬度）。
//   2. NavLinkCache：最近一条 REPLY 的 NavExt 摘要；断连必须 reset()，保证重连后第一条 REPLY
//      到达前按「无数据」处理（S2：旧弯道点不能被写回）。
//   3. MapTargetWriter：CURVE_AHEAD + 本机 GNSS 有效性 → MapTargetVelocities JSON（≤2 Hz，
//      失效/删点/降速会在 ≤1 个 calib 拍内清空或更新）。位置 LastGPSPosition 由
//      last_gps_position_json() 按 GNSS 节拍生成（S3）。
//   4. clear_map_targets()：退出/上路清理，由 main 注入 Params 操作（阻塞 put/remove）。
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
#include "frame_meta.h"  // GnssSample（票 B 的 MetaProvider 快照）

// REPLY 的 NavExt 快照。has_* 来自 present_flags；四个子结构只在对应 has_* 为真时有意义
// （parse_reply 已把 present=0 的子块读作 0）。
struct NavStateSnapshot {
  uint32_t frame_index = 0;
  uint64_t timestamp_eof_ns = 0;
  bool gps_ok = false;
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

 private:
  uint64_t last_reply_ns_ = 0;
  uint64_t t_eof_ = 0;
  uint8_t present_flags_ = 0;
  uint8_t nav_health_flags_ = 0;
  bgm1::CurveAhead curve_;
};

// 协议 §1.4 的 T_acc：车机 gps_ok 与手机一致，水平精度 ≤ 30 m。
constexpr double kGpsHorizontalAccuracyMaxM = 30.0;
constexpr uint64_t kNavDataMaxAgeNs = 2'000'000'000;  // §1.4 T_nav：NavExt 数据年龄
// N3：GNSS 断流时不能靠 has_fix=0（没有消息就不会有它）；位置/弯道目标用更短的 1 s
constexpr uint64_t kGpsFixMaxAgeNs = 1'000'000'000;
// N1 的 (lat,lon) 同一性匹配容差：50 × 1e-7° ≈ 0.55 m。手机重算/重采样会让同一点有厘米级抖动；
// 若用严格相等，N10 的「点坐标每拍微动」会全部被判成删点，退回 20 Hz 写。
// 注意：这是方框容差，纬度、经度分别比较；经度方向的实际距离是 0.55·cosφ m。
constexpr int32_t kSamePointToleranceX1e7 = 50;

// AU-3：精度迟滞的阈值。不可用 → 可用要 ≤ kGpsHorizontalAccuracyMaxM；可用 → 不可用要
// > kGpsHorizontalAccuracyDropM，或连续 kGpsAccuracyDropSamples 条 > kGpsHorizontalAccuracyMaxM。
constexpr double kGpsHorizontalAccuracyDropM = 40.0;
constexpr int kGpsAccuracyDropSamples = 3;

// 车机自己的定位是否可用写 LastGPSPosition：有 fix ∧ fix ≤1 s ∧ event_valid ∧
// 精度已知且 ≤30 m ∧ 坐标有效（N3/N6/S7）。无状态：calib 线程按 GNSS 节拍逐条判。
bool gps_fix_usable(const GnssSample& gnss, uint64_t now_ns);
// 同上但不看精度（有 fix ∧ fix ≤1 s ∧ event_valid ∧ 坐标有效）：GpsAccuracyGate 的前提。
bool gps_fix_present(const GnssSample& gnss, uint64_t now_ns);

// AU-3：弯道目标用的定位可用性，带精度迟滞，避免精度在 30 m 上下抖动时目标反复清空写回。
// 计数按 GNSS 样本（message_time_ns 变化）推进，20 Hz 的 reply 拍重复看到同一条不累加。
// 有状态，reply 线程独占（MapTargetWriter 的成员）。未知精度（≤0 / NaN）视同不达标：
// 不能让不可用恢复，可用时计入「连续 >30 m」但不单条清空。
class GpsAccuracyGate {
 public:
  bool usable(const GnssSample& gnss, uint64_t now_ns);
  void reset();

 private:
  bool usable_ = false;
  int consecutive_over_ = 0;
  uint64_t last_sample_ns_ = 0;
};
std::optional<std::string> last_gps_position_json(const GnssSample& gnss, uint64_t now_ns);

// MapTargetWriter 的全部输入。GNSS 直接取 MetaProvider 里票 B 已有的最近一条样本，
// 不新增订阅；last_reply_ns = 最近一条成功解析的 REPLY 到达时刻（0 = 无数据/已断连）。
struct MapTargetInput {
  bool link_connected = false;
  bool curve_present = false;
  bgm1::CurveAhead curve;
  uint8_t nav_health_flags = 0;  // REPLY 的 nav_health_flags（AU-4：rerouting / route_ok）
  uint64_t last_reply_ns = 0;  // 仅用于事实记录；数据新鲜度按 t_eof − age_ms（N7）
  uint64_t t_eof = 0;          // REPLY 回显的帧 timestamp_eof（ns，同 nanos_since_boot）
  bool gnss_seen = false;
  GnssSample gnss;  // MetaProvider::latest_gnss() 的原样快照
  uint64_t now_ns = 0;
};

// 写 MapTargetVelocities 的时机：
//   - 有目标点被删除（含清空）或容差内同一点目标速度下调 → 下一拍写（≤1 个 calib 拍：正常 ≤50 ms，
//     modeld 不发导致 calib 只能按 1 s 超时醒来时最长约 1 s；SCC-map 对旧点有粘滞，N1/N2）。
//   - 其他内容变化（上新点/提速）：≥500 ms（≤2 Hz）。
//   - 内容不变：≥1 s 刷新（有意为之：可自愈被外部改写的值）。
//   - 首次调用必写（清掉上趟进程遗留的弯道点）。
//   - CURVE present=0 / 链路断开 / 数据超过 2 s（按 t_eof − age_ms 合成）→ []；
//     GNSS 无 fix、event_valid=0、fix 超过 1 s 或水平精度不可用（AU-3 迟滞，见 GpsAccuracyGate）→ []。
//   - AU-4：rerouting=1 → []；curve_flags.bit0（沿路线）为 1 而 route_ok=0 → []
//     （road_lookahead 的点不依赖路线，route_ok=0 照常写）。
//   - 目标速度为 0、坐标非法或 (0,0) 的点跳过。
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
  };

  static bool point_usable(const bgm1::CurvePoint& p);
  static int usable_point_count(const MapTargetInput& in);
  bool same_points(const MapTargetInput& in, int n) const;
  // N1/N2：上次写出的点被删除，或容差内同一点速度下调 → 下一拍写（≤1 个 calib 拍）
  bool urgent_change(const MapTargetInput& in, int n) const;

  GpsAccuracyGate gps_gate_;
  bool wrote_ = false;
  uint64_t last_write_ns_ = 0;
  int last_point_count_ = 0;
  WrittenPoint last_points_[bgm1::kCurveMaxPoints];
};

// reply 线程在一次决策里用到的「现在」：GNSS 快照 + 时刻。先取 GNSS 再取 now（AU-13），
// 这样刚到的新 GNSS 样本不会被判成「未来时刻」而误清空。
struct GnssSnapshot {
  bool seen = false;
  GnssSample gnss;
};
struct NavLinkTick {
  GnssSnapshot gnss;
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
  // make_tick 只在 epoch 变化时才调用：reply 线程每轮循环都进这里，GNSS 快照要加 MetaProvider 的锁，
  // 绝大多数轮次不需要。make_tick 返回 NavLinkTick（先取快照后取 now，AU-13）。
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
  // 没有新 REPLY 的拍：NavExt / GNSS 过期时清掉旧弯道点。
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

// 退出清理 / 上路清理的 Params 操作（main 注入：mem_params.put / remove，阻塞调用）。
struct MapParamsOps {
  std::function<void(const char*, const std::string&)> put;
  std::function<void(const char*)> remove;
};
// MapTargetVelocities = "[]"、删除 LastGPSPosition。
void clear_map_targets(const MapParamsOps& ops);
