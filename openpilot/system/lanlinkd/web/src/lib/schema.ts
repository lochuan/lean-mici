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
  options?: OptionChoice[];
  min?: number;
  max?: number;
  step?: number;
  unit?: Unit;
  enablement?: Rule[];
  visibility?: Rule[];
  /** 由后端 settings.mark_missing_keys 注入：设备上不存在此 param */
  missing?: boolean;
}

export interface SubPanel {
  id: string;
  label: string;
  trigger_key?: string;
  trigger_condition?: Rule;
  items: Item[];
}

export interface Section {
  id: string;
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
  sections: Section[];
}

export interface SettingsSchema {
  schema_version: string;
  panels: Panel[];
  vehicle_settings?: Record<string, unknown>;
}

/** /api/capabilities：19 个字段，见 status_snapshot.build_capabilities */
export type Capabilities = Record<string, string | number | boolean>;

/** /api/params/_all：key -> 规范字符串（BOOL 为 "1"/"0"） */
export type ParamValues = Record<string, string>;

/** /api/status 快照 */
export interface StatusSnapshot {
  stale?: boolean;
  paramsVersion?: string | null;
  device?: {
    cpuTempC?: number[];
    gpuTempC?: number[];
    memoryTempC?: number;
    memoryUsagePercent?: number;
    freeSpacePercent?: number;
    usbOnline?: boolean;
    networkType?: string;
    networkStrength?: string;
    thermalStatus?: string;
  };
  car?: {
    vEgo?: number;
    standstill?: boolean;
    gearShifter?: string;
    steeringAngleDeg?: number;
    gas?: number;
    brake?: number;
    leftBlinker?: boolean;
    rightBlinker?: boolean;
    leftBlindspot?: boolean;
    rightBlindspot?: boolean;
    fuelGauge?: number;
    batteryPercent?: number;
  };
  system?: { version?: string; branch?: string; commit?: string; ignition?: boolean };
  gps?: {
    latitude?: number;
    longitude?: number;
    altitude?: number;
    speed?: number;
    satelliteCount?: number;
  };
  capabilities?: Capabilities;
}

export interface ModelBundle {
  ref: string;
  displayName: string;
  internalName: string;
  index?: number;
  generation?: number;
  environment?: string;
  runner?: string;
  is20hz?: boolean;
  folder?: string;
  fav?: boolean;
  active?: boolean;
}

export interface ModelsState {
  default_model: string;
  active: ModelBundle | null;
  queued_ref: string | null;
  favs: string[];
  bundles: ModelBundle[];
  download?: {
    ref?: string;
    status?: string;
    progress?: number;
    eta?: number;
  } | null;
}
