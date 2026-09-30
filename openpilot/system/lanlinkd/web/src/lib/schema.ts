/** settings_ui.json 的类型定义。
 *
 * 形状来自实测的 schema（本地 9 panel / 71 item，规则结构与 upstream 一致）。
 * 关键一点：滑块**没有**独立 widget 类型——upstream 的 page.schema.json
 * widget enum 只有 toggle/option/multiple_button/button/info，`option` 按
 * 是否带 min/max/step 分叉成滑块或下拉。见 FRONTEND_SPEC.md §3。
 */

export type Widget = "toggle" | "option" | "multiple_button" | "button" | "info";

/** 在线标定摘要（/api/avoidance 透传的 extrinsicsCalibration 字段）。
 *  eagled 在相机未标定时整体关掉视觉路径——地平面投影的 dRel 对 pitch
 *  的敏感度在 40m 处是 0.5° → 41%，未标定的 pitch 会直接生成虚假偏移。
 *  这份状态独立于 eagled 是否运行：lanlinkd 自己订阅标定消息。 */
export interface CalState {
  calStatus: string; // "uncalibrated" | "calibrated" | "recalibrating" | "unknown"
  calPerc: number; // 0-100
  calValid: boolean; // valid && calStatus=="calibrated" && rpyCalib 长度为 3
  visionGated: boolean; // = !calValid，前端据此解释 nVision=0
}

export interface Rule {
  type:
    | "param"
    | "param_compare"
    | "capability"
    | "calibrated" // 在线标定完成（extrinsicsCalibration）；数据来自 store.cal
    | "offroad_only"
    | "not_engaged"
    | "any"
    | "all"
    | "not";
  key?: string;
  field?: string;
  equals?: unknown;
  op?: "<" | "<=" | ">" | ">=" | "==" | "!=";
  value?: number;
  condition?: Rule;
  conditions?: Rule[];
}

export interface OptionChoice {
  value: number | string;
  label: string;
  enablement?: Rule[];
  visibility?: Rule[];
}

/** 单位：字符串（"meters"）或按单位制分叉（跟随 IsMetric） */
export type Unit = string | { metric: string; imperial: string };

export interface Item {
  key: string;
  widget: Widget;
  title: string;
  description?: string;
  /** 长警告文案（如 AutoLaneChangeTimer 的使用注意） */
  details?: string;
  /** 标题后缀随另一个 param 变化，如 "(Real-Time & Offline)" / "(Offline Only)" */
  title_param_suffix?: { param: string; values: Record<string, string> };
  options?: OptionChoice[];
  min?: number;
  max?: number;
  step?: number;
  unit?: Unit;
  /** 行内展开的子设置（父项开启时显示），如 BlinkerPauseLateralControl */
  sub_items?: Item[];
  enablement?: Rule[];
  visibility?: Rule[];
  /** schema 声明的不可远程修改项（AdbEnabled/SshEnabled）；后端同样返回 403 */
  blocked?: boolean;
  /** 需要一次上下电循环才生效 */
  needs_onroad_cycle?: boolean;
  /** 由后端 settings.mark_missing_keys 注入：设备上不存在此 param。
   *  注意是下划线前缀，与 settings.py 的输出一致。 */
  _missing?: boolean;
}

export interface SubPanel {
  id: string;
  label: string;
  /** 抽屉的前置开关；trigger_condition 为 null 表示无条件可进入 */
  trigger_key?: string;
  trigger_condition?: Rule | null;
  items: Item[];
}

export interface Section {
  id?: string;
  title?: string;
  description?: string;
  items?: Item[];
  sub_panels?: SubPanel[];
  enablement?: Rule[];
  visibility?: Rule[];
}

export interface Panel {
  id: string;
  label: string;
  icon?: string;
  order?: number;
  description?: string;
  /** 上游标记为可云端配置；本地只用于展示 */
  remote_configurable?: boolean;
  sections: Section[];
}

/** 按品牌分组的车型专属设置，依 capabilities.brand 选用 */
export interface VehicleBrandSettings {
  title: string;
  items: Item[];
}

export interface SettingsSchema {
  schema_version: string;
  panels: Panel[];
  vehicle_settings?: Record<string, VehicleBrandSettings>;
}

/** /api/capabilities：19 个字段，见 status_snapshot.build_capabilities */
export type Capabilities = Record<string, string | number | boolean>;

/** /api/params/_all：key -> 规范字符串（BOOL 为 "1"/"0"） */
export type ParamValues = Record<string, string>;

/** /api/status 快照（只声明前端实际用到的字段）。
 *
 * device / car / gps 三组遥测里，car 和 gps 仍然故意不建模：那些数据在
 * 车机屏幕上已经有了。device 建模给「状态」页用（CPU/GPU 负载与温度），
 * 字段照 status_snapshot.build_snapshot() 的 device 节。
 *
 * 轮询本身不能停——paramsVersion 是察觉车机端改了设置的唯一途径。
 */
export interface DeviceStatus {
  /** manager 判定的 onroad 状态（hardwared 的 started 位），与点火无关 */
  started?: boolean;
  /** cereal NetworkType 枚举（0 none / 1 wifi / … / 6 ethernet） */
  networkType?: number;
  cpuTempC?: number[];
  gpuTempC?: number[];
  memoryTempC?: number;
  maxTempC?: number;
  memoryUsagePercent?: number;
  cpuUsagePercent?: number[];
  gpuUsagePercent?: number;
  freeSpacePercent?: number;
  powerDrawW?: number;
  fanSpeedPercentDesired?: number;
  thermalStatus?: number; // 0 ok / 2 overheated / 3 critical
}

export interface StatusSnapshot {
  stale?: boolean;
  paramsVersion?: string | null;
  system?: { version?: string; branch?: string; commit?: string; ignition?: boolean };
  device?: DeviceStatus;
  capabilities?: Capabilities;
}

/** 避让监测目标（eagleDebug targets 条目；雷达点与视觉目标共用） */
export interface EagleTarget {
  dRel: number; // m，车头原点
  yRel: number; // m，左正
  vRel: number; // m/s，雷达点才有，视觉目标 0
  cls: string; // person/bicycle/motorcycle/car；雷达点 ""
  conf: number; // YOLO conf，雷达点 0
  weight: number; // planner 权重（VRU 1.0 / car 0.6）
  matched: boolean; // 雷达↔视觉关联上
  inGate: boolean; // 三级门控判决（tier 1 车道线相对 / tier 2 路径相对 / tier 3 固定带）
  vision: boolean; // true=YOLO 投影目标；false=雷达点
  pairId: number;
  lane: number; // C2 车道归属：-1 左邻 / 0 本道或重叠 / +1 右邻（分类未参与时 0）
}

export interface AvoidanceSnapshot {
  stale?: boolean;
  logMonoTime?: number;
  valid?: boolean;
  active?: boolean;
  direction?: number;
  yDes?: number; // 期望横向偏移 m，左正
  bias?: number; // 曲率偏置 1/m
  maxOffset?: number; // 预算折算后的本帧生效上限 m
  bsmLeft?: boolean;
  bsmRight?: boolean;
  vEgo?: number; // m/s
  nRadar?: number;
  nVision?: number;
  nAssociated?: number;
  edgeClearance?: number; // 避让侧路沿余量 m；inf 时后端发 999.0
  canError?: boolean; // radarTracks.errors.canError（随 debug 透传）
  radarUnavailable?: boolean; // radarTracks.errors.radarUnavailableTemporary
  targets?: EagleTarget[];
  // C2/C7/C9 新增字段——旧后端不发时 undefined，UI 必须容忍
  laneLeftValid?: boolean; // 本道左边界线置信（tier 1 该侧可用）
  laneRightValid?: boolean; // 本道右边界线置信
  budgetLeft?: number; // 左侧横向预算 m；999.0 = 无侧向约束
  budgetRight?: number; // 右侧横向预算 m
  changeClearLeft?: boolean; // 目标道（左）变道清空（时间投影）
  changeClearRight?: boolean; // 目标道（右）变道清空

  // 标定状态：lanlinkd 自己订阅 extrinsicsCalibration 透传。eagled 在
  // 相机未标定时整体关掉视觉路径，否则前端只会看到 nVision 恒为 0 而无从解释。
  // EagleDebug 的 capnp 结构里没有降级原因字段，而加字段要设备全量重建。
  calStatus?: string; // "uncalibrated" | "calibrated" | "recalibrating" | "unknown"
  calPerc?: number; // 标定进度 0-100
  calValid?: boolean; // 消息 valid 且 calStatus=="calibrated" 且 rpyCalib 长度为 3
  visionGated?: boolean; // 视觉路径是否被标定门关掉（= !calValid）

  // 车道几何（lanlinkd 自己订阅 modelV2，lanes.py）：modelV2 停更/无帧为 null
  lanes?: LaneSnapshot | null;
}

/** lanes.py lane_snapshot 的 wire 形状。两套同网格几何（y 均为左正、
 *  x 网格 0-60m，坐标语义由后端 model_geometry 独占解释，前端只画图）：
 *  - corrected：车体系（x 前保险杠原点）——鸟瞰图默认层，与雷达目标同原点。
 *  - raw：零安装偏移的车体系（ctf=0 过同一换算口，y 同为左正；x 原点即相机）
 *    ——叠加层即精修仪器，两套错位 = 安装偏移。
 *  line.y null = 该线本轮不可用。 */
export interface LaneLineSnap {
  /** 共享网格上的 y 值（m，左正） */
  y: number[] | null;
  prob?: number | null; // 车道线：laneLineProbs（外侧线透明度用）
  std?: number | null; // laneLineStds / roadEdgeStds
}

export interface LaneGeometrySet {
  /** [远左外线, 本道左边界, 本道右边界, 远右外线]；单项可为 null */
  laneLines: (LaneLineSnap | null)[];
  /** [左路沿, 右路沿] */
  roadEdges: (LaneLineSnap | null)[];
  /** modelV2 预测路径（本车轨迹） */
  path?: { y: number[] | null; std: number[] | null } | null;
}

export interface LaneSnapshot {
  x: number[];
  corrected: LaneGeometrySet;
  raw: LaneGeometrySet;
}

/** /api/calibration/status：在线标定会话状态（CalibrationController）。
 *  last_result 是 fit_calibrated_offsets 的输出 + camera_to_front（建议值与
 *  保存防呆结论）+ saved；insufficient = 配对数低于保存下限 30。
 *  POST /api/calibration/apply 一键把建议值写进 Params CameraToFront，
 *  防呆拒绝时返回 409（票 #7）。
 *
 *  pitch/yaw 已不再由这里拟合：投影改用 openpilot 的 extrinsicsCalibration
 *  实时 rpy，CAMERA_PITCH/CAMERA_YAW 常数不再被读取，手工拟合它们只会给出
 *  不起作用的数字。剩下 CAMERA_TO_FRONT（纵向安装偏移）是在线标定不提供、
 *  因而仍需手工标的唯一量。
 *
 *  通过判据改为按距离分档：单一全局 0.30m 阈值在 10m 以外物理不可达（地平面
 *  投影的 dRel 对 pitch 的敏感度使 40m 处需要 0.013° 精度，而车辆俯仰变化
 *  就有 1° 量级）。25m 以上只考核方位角残差 —— 那是单目真正测得准的量。 */
export interface CalibrationBand {
  n: number;
  p95_m?: number;
  bearing_p95_deg?: number;
  pass: boolean | null; // null = 该档无数据，不能算通过
}

export interface CalibrationResult {
  n_pairs: number;
  v_ego_min?: number;
  v_ego_max?: number;
  d_front_m: number;
  lateral_bias_m: number;
  forward_p95_before_m?: number;
  forward_p95_after_m?: number;
  lateral_p95_m?: number;
  residual_p95_before_m: number;
  residual_p95_after_m: number; // = max(forward_after, lateral)
  bands?: Record<string, CalibrationBand>;
  warnings?: string[];
  pass: boolean;
  insufficient?: boolean;
  camera_to_front: CameraToFrontProposal;
  saved: boolean; // 已经 POST /api/calibration/apply 写进 Params
}

/** propose_camera_to_front 的保存防呆结论：proposed = 当前值 + d_front（增量）；
 *  配对 < 30 或越出 0.5–2.5m 时 savable=false，reject_reason 给出原因。 */
export interface CameraToFrontProposal {
  current_m: number;
  proposed_m: number;
  savable: boolean;
  reject_reason: string | null;
}

export interface CalibrationStatus {
  running: boolean;
  n_pairs: number;
  elapsed_s: number;
  last_error?: string | null;
  last_result?: CalibrationResult | null;
}

/** 车辆指纹状态（GET /api/vehicle，见 vehicle_api.vehicle_state） */
export interface VehicleState {
  /** manual = 用户手动指定；auto = 自动指纹识别；none = 未识别 */
  source: "manual" | "auto" | "none";
  name: string;
  platform: string;
  brand: string;
  fingerprinted: boolean;
  detected: {
    platform?: string;
    brand?: string;
    vin?: string;
    steer_control_type?: string;
    pcm_cruise?: boolean;
    openpilot_longitudinal?: boolean;
    alpha_long_available?: boolean;
    enable_bsm?: boolean;
    radar_unavailable?: boolean;
    mass_kg?: number;
    wheelbase_m?: number;
  };
  choices: string[];
}

/** /api/bluetooth：BluetoothStatus 的序列化形（见 bluetooth_api.status_payload）。
 *  字段与 sunnypilot/system/bluetooth/protocol.py 的 dataclass 一一对应。 */
export interface BluetoothDevice {
  address: string;
  name: string;
  paired: boolean;
  trusted: boolean;
  connected: boolean;
  blocked: boolean;
  rssi: number | null;
  uuids: string[];
  audio: boolean;
  controller: boolean;
}

/** daemon 的 bluez agent 配对请求（confirmation/authorization 只需确认，
 *  pin/passkey 需要输入数值；display_only 只展示不响应） */
export interface BluetoothPrompt {
  id: string;
  kind: "confirmation" | "authorization" | "pin" | "passkey" | (string & {});
  name?: string;
  value?: string;
  display_only?: boolean;
  address?: string;
}

export interface BluetoothStatus {
  available: boolean;
  enabled: boolean;
  powered: boolean;
  discovering: boolean;
  offroad: boolean;
  selected_audio: string;
  pairing_address: string;
  devices: BluetoothDevice[];
  prompt: BluetoothPrompt | null;
  error: string;
}

/** /api/wifi：一个可连接网络（lanlinkd wl_worker 的序列化形） */
export interface WifiNetwork {
  ssid: string;
  rssi: number | null;
  security: string;
  saved: boolean;
}

/** 当前连接 profile 的 ipv4 概览（wifi_manager.get_ipv4_settings 形） */
export interface WifiIpv4 {
  method: string;
  addresses: string[];
  gateway: string;
  dns: string[];
}

export interface WifiStatus {
  available: boolean;
  offroad: boolean;
  connecting: string | null;
  connected: string | null;
  ipv4: WifiIpv4;
  networks: WifiNetwork[];
  error: string;
}

/** /api/software：updater 状态参数的序列化形（见 software_api.status） */
export interface SoftwareDescription {
  version: string;
  branch: string;
  commit: string;
  date: string;
}

export interface SoftwareStatus {
  version: string;
  branch: string;
  commit: string;
  current: SoftwareDescription | null;
  updaterState: string;
  updateAvailable: boolean;
  fetchAvailable: boolean;
  failedCount: number;
  failed: boolean;
  newVersion: SoftwareDescription | null;
  targetBranch: string;
  availableBranches: string[];
  offroad: boolean;
}
