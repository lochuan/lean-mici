/** 导航元数据：侧边栏与首页共用的分组、图标和入口摘要，保证两处一致。
 *
 * 分组按面板 id 归类，组内顺序沿用 schema 的 order；schema 新增而这里
 * 未登记的面板落到 DEFAULT_GROUP，入口不会丢。
 */
import { computed, type Component } from "vue";
import {
  Activity,
  Car,
  CarFront,
  Code2,
  Cog,
  Eye,
  Gauge,
  Monitor,
  Package,
  Settings2,
  SlidersHorizontal,
  Stethoscope,
  Wifi,
  Wrench,
} from "lucide-vue-next";
import { panels } from "./store";
import type { Panel } from "./schema";

/** schema 的 icon 名 → lucide 组件 */
const ICONS: Record<string, Component> = {
  steering_wheel: Car,
  cruise_control: Gauge,
  display: Monitor,
  visuals: Eye,
  toggles: SlidersHorizontal,
  device: Cog,
  wifi: Wifi,
  status: Activity,
  software: Package,
  diagnostics: Stethoscope,
  toyota: Wrench,
  vehicle: CarFront,
  developer: Code2,
};

export const iconFor = (p: Panel): Component => ICONS[p.icon ?? ""] ?? Settings2;

const GROUPS: { id: string; label: string; ids: string[] }[] = [
  { id: "drive", label: "驾驶设置", ids: ["steering", "cruise", "display", "visuals", "toggles"] },
  { id: "device", label: "设备与连接", ids: ["device", "bluetooth", "status", "software"] },
  { id: "maintenance", label: "诊断与维护", ids: ["diagnostics", "toyota", "vehicle", "developer"] },
];

/** 未登记的 schema 面板多为普通设置页，归入驾驶设置 */
const DEFAULT_GROUP = "drive";

/** 由自定义组件驱动的专用页（与 PanelView 的分派一致），入口展示功能说明而非设置项数 */
const DEDICATED: Record<string, true> = {
  bluetooth: true,
  status: true,
  diagnostics: true,
  toyota: true,
  software: true,
  vehicle: true,
};

export interface NavGroup {
  id: string;
  label: string;
  panels: Panel[];
}

export const navGroups = computed<NavGroup[]>(() =>
  GROUPS.map((g) => ({
    id: g.id,
    label: g.label,
    panels: panels.value.filter(
      (p) => (GROUPS.find((x) => x.ids.includes(p.id))?.id ?? DEFAULT_GROUP) === g.id,
    ),
  })).filter((g) => g.panels.length > 0),
);

/** 入口副标题：专用页给功能说明，设置页给设置项数 */
export function panelSummary(p: Panel): string {
  if (DEDICATED[p.id]) return p.description ?? "";
  const count = p.sections.reduce(
    (n, s) => n + (s.items?.length ?? 0) + (s.sub_panels ?? []).reduce((m, d) => m + d.items.length, 0),
    0,
  );
  return `${count} 项设置`;
}
