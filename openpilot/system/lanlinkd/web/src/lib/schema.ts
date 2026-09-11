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
 * 后端还会返回 device / car / gps 三组遥测（CPU 温度、车速、GPS 等），
 * 这里故意不建模：LANLink 是设置工具，不做仪表盘，那些数据在车机屏幕上
 * 已经有了。真要加回来时照 status_snapshot.build_snapshot() 补即可。
 *
 * 但轮询本身不能停——paramsVersion 是察觉车机端改了设置的唯一途径。
 */
export interface StatusSnapshot {
  stale?: boolean;
  paramsVersion?: string | null;
  system?: { version?: string; branch?: string; commit?: string; ignition?: boolean };
  capabilities?: Capabilities;
}

export interface ModelBundle {
  ref: string;
  displayName: string;
  internalName: string;
  index: number;
  generation?: number;
  environment?: string;
  runner?: string;
  is20hz?: boolean;
  /** 分组名，来自 bundle 的 folder override（如 "2026 World Models"） */
  folder?: string;
  fav?: boolean;
  active?: boolean;
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

/** 当前激活模型：只有四个字段，不是完整 bundle（见 models_state） */
export interface ActiveModel {
  ref: string;
  displayName: string;
  internalName: string;
  runner: string;
}

export interface ModelsState {
  default_model: string;
  active: ActiveModel | null;
  queued_ref: string | null;
  favs: string[];
  bundles: ModelBundle[];
  download?: {
    ref?: string;
    status?: string;
    progress?: number;
    eta?: number;
  } | null;
  /** 已下载模型占用空间（MB），用于 Clear Cache 按钮旁提示 */
  cache_size_mb?: number;
}
