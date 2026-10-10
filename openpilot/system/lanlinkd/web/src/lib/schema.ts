/** settings_ui.json 的类型定义。
 *
 * 形状来自实测的 schema（本地 9 panel / 71 item，规则结构与 upstream 一致）。
 * 关键一点：滑块**没有**独立 widget 类型——upstream 的 page.schema.json
 * widget enum 只有 toggle/option/multiple_button/button/info，`option` 按
 * 是否带 min/max/step 分叉成滑块或下拉。见 FRONTEND_SPEC.md §3。
 */

export type Widget = "toggle" | "option" | "multiple_button" | "button" | "info";

export interface Rule {
  type:
    | "param"
    | "param_compare"
    | "capability"
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

/** 最近 N 帧 modelV2.big：1=大模型出的帧，0=小模型兜底；最旧在前 */
export interface ModelStatus {
  bigEnabled?: boolean;
  linkState?: string; // BigmodelLinkState：connecting/connected/blip/restart/lost
  frames?: number[];
  timing?: ModelTiming;
}

export interface P50P90 {
  p50: number;
  p90: number;
}

/** 最近 N 帧 C4 本机分段（eof 起沿关键路径首尾相接，total = 到 modeld 收到）与小模型原因计数 */
export interface ModelTiming {
  window: number;
  stagesMs: Record<
    "capture" | "warp" | "pairWait" | "encode" | "send" | "replyWait" | "network" | "total",
    P50P90
  > | null;
  sources: Record<"off" | "big" | "warmup" | "linkDown" | "timeout" | "late" | "zeroOutput", number>;
  deadlineMs: number | null;
}

/** 单个速度档（m/s）的自整定结果；calPerc 为该档学习进度（旧缓存无此字段时为 null） */
export interface TorqueBin {
  center: number;
  lo: number;
  hi: number;
  latAccelFactor: number;
  friction: number;
  valid: boolean;
  calPerc: number | null;
}

/** torqued 的 lateralTorqueParameters（实时消息或 LiveTorqueParameters 落盘缓存） */
export interface TorqueLearned {
  valid: boolean;
  useParams: boolean;
  latAccelFactor: number;
  friction: number;
  latAccelOffset: number;
  latAccelFactorRaw: number;
  frictionRaw: number;
  calPerc: number;
  totalPoints: number;
  decay: number;
  resets: number;
  bins: TorqueBin[];
}

/** 横向扭矩自整定卡片（status_snapshot.build_torque_status） */
export interface TorqueStatus {
  source: "live" | "cache" | "none";
  learned: TorqueLearned | null;
  offline: { lateralControl: string; latAccelFactor: number | null; friction: number | null };
  toggles: { EnforceTorqueControl: boolean; LiveTorqueParamsToggle: boolean; SpeedDependentTorqueToggle: boolean };
}

export interface StatusSnapshot {
  stale?: boolean;
  paramsVersion?: string | null;
  system?: { version?: string; branch?: string; commit?: string; ignition?: boolean };
  device?: DeviceStatus;
  model?: ModelStatus;
  torque?: TorqueStatus;
  capabilities?: Capabilities;
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

/** /api/diagnostics：诊断页（相机安装、跑偏、画龙、转向能力）。
 *  行驶统计只算本次行驶（started 由假变真时清零，停车后保留）。 */
export interface DiagnosticsReport {
  drive: { started: boolean; seconds: number };   // seconds = 本次行驶已过秒数（未行驶过为 0）
  camera: CameraDiag | null;                       // 从未标定过（无缓存也无实时）为 null
  drift: DriftDiag;
  weave: WeaveDiag;
  steering: SteeringDiag;
}
export interface CameraDiag {
  source: "live" | "cache";
  calStatus: "uncalibrated" | "calibrated" | "invalid" | "recalibrating";
  calPerc: number;                 // 0–100
  pitchDeg: number;                // >0 设备朝下
  yawDeg: number;                  // >0 设备朝左
  limits: { pitchUpDeg: number; pitchDownDeg: number; yawDeg: number };  // 均为正数
  cameraOffsetM: number;           // 当前 CameraOffset，>0 = 设备在中线左侧
}
export interface DriftDiag {
  seconds: number;                 // 有效直道样本秒数
  carOffsetM: number | null;       // 平均，>0 车偏右；无样本 null
  planOffsetM: number | null;      // 平均，>0 规划偏右
  angleOffsetDeg: number | null;   // paramsd angleOffsetAverageDeg（实时或缓存）
  angleOffsetValid: boolean | null;
  accurateAngle: "ready" | "pending" | "unknown" | "n/a";  // Toyota 高精度转角；非 Toyota 为 n/a；本次没收到 carState 为 unknown
  latAccelOffset: number | null;   // m/s²，torqued 学到的偏置
}
export interface WeaveDiag {
  seconds: number;                 // 直道样本秒数（与 drift 同口径）
  windows: number;                 // 已评估的 10 s 窗口数
  weaveWindows: number;            // 其中判为画龙的窗口数
  medianP2pM: number | null;       // 各窗口峰峰值中位数
  medianPeriodS: number | null;    // 各窗口周期中位数（无法估计周期的窗口不计）
  trace: (number | null)[];        // 最近 30 s，4 Hz，共 120 点，米，>0 偏右；null = 不满足采样条件
  lateralDelayS: number | null;    // lagd 学到的横向延迟
  factoryDelayS: number | null;    // CarParams.steerActuatorDelay
}
export interface SteeringDiag {
  control: "torque" | "angle" | "";
  activeSeconds: number;           // 横向控制激活且车速 > 5 m/s 的秒数
  curveSeconds: number;            // 其中弯道秒数
  saturatedCurveSeconds: number;   // 弯道且饱和的秒数
  usage: number[] | null;          // torque 控制：|output| 五档 [0,.2) [.2,.4) [.4,.6) [.6,.8) [.8,1] 的秒数；angle 为 null
  epsTempFaults: number;
  epsPermanent: boolean;
  maxLatAccel: number | null;      // 激活时达到的最大 |curvature·v²|，m/s²
}
