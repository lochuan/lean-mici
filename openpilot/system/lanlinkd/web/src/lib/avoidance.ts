/** 避让监测的纯函数层：类别配色 + 状态推导 + 偏移箭头几何。
 *
 * 与 radar.ts 同款分工：可测的判定逻辑全抽在这里，AvoidanceView.vue
 * 只做渲染。方向语义（review 裁定）：
 *  - yDes > 0 = 向左偏（与 yRel 左正同号），箭头按 yDes 符号画；
 *  - direction = 障碍物侧（+1 = 障碍在右），只做侧别标识，不画箭头。
 */
import { clamp, lateralX, type RadarViewBox } from "./radar";

// ---- 视觉目标类别 ----

export type TargetCls = "person" | "bicycle" | "motorcycle" | "car" | "other";

export function targetCls(cls: string): TargetCls {
  return cls === "person" || cls === "bicycle" || cls === "motorcycle" || cls === "car"
    ? (cls as TargetCls)
    : "other";
}

/** person 红 / 两轮橙 / car 蓝，与 sl-* 语义色一一对应 */
export const CLS_FILL: Record<TargetCls, string> = {
  person: "fill-sl-danger",
  bicycle: "fill-sl-warn",
  motorcycle: "fill-sl-warn",
  car: "fill-sl-info",
  other: "fill-sl-text-3",
};

/** HTML 图例用的背景色（svg 里是 fill-*，span 里是 bg-*） */
export const CLS_BG: Record<TargetCls, string> = {
  person: "bg-sl-danger",
  bicycle: "bg-sl-warn",
  motorcycle: "bg-sl-warn",
  car: "bg-sl-info",
  other: "bg-sl-text-3",
};

export const CLS_LABEL: Record<TargetCls, string> = {
  person: "行人",
  bicycle: "自行车",
  motorcycle: "摩托车",
  car: "汽车",
  other: "其他",
};

// ---- 状态条推导 ----

export interface AvoidanceStatus {
  label: string;
  badge: "accent" | "muted" | "warn";
}

/** active&&!stale → ACTIVE（绿）；valid&&!active → STANDBY（灰）；stale → 无数据（黄） */
export function avoidanceStatus(s: {
  valid?: boolean;
  active?: boolean;
  stale?: boolean;
}): AvoidanceStatus {
  if (s.stale) return { label: "无数据", badge: "warn" };
  if (s.active) return { label: "ACTIVE", badge: "accent" };
  return { label: "STANDBY", badge: "muted" };
}

// ---- 偏移箭头几何 ----

export interface ArrowGeometry {
  /** 起点 x（ego 中心） */
  x1: number;
  /** 终点 x（yDes 投影，已钳制） */
  x2: number;
  /** 钳制后的 yDes（标签用） */
  yDes: number;
}

/** yDes → 横向箭头终点。yDes 左正（与 yRel 同号），lateralX 会把正值
 *  映到屏幕左，所以"向左偏"自然画向左边。yDes 超出 ±lateralM 钳到边缘。 */
export function offsetArrow(yDes: number, vb: RadarViewBox, egoX: number): ArrowGeometry {
  const y = clamp(yDes, -vb.lateralM, vb.lateralM);
  return { x1: egoX, x2: lateralX(y, vb), yDes: y };
}

// ---- 障碍侧标识 ----

/** direction：-1 左 / 0 无 / 1 右（障碍物侧，不用于画箭头） */
export function obstacleSide(direction: number | undefined): string {
  if (direction === undefined) return "—";
  if (direction > 0) return "障碍在右";
  if (direction < 0) return "障碍在左";
  return "—";
}

// ---- 雷达↔视觉配对 ----

export interface Pairable {
  vision: boolean;
  matched: boolean;
  pairId: number;
}

/** 提取配对（matched 且 pairId≠0 的双方共享同 id）。返回视觉目标↔雷达点对，
 *  供视图连线；单边出现的（理论不该有）不连线。 */
export function pairMembers<T extends Pairable>(targets: T[]): Array<{ vision: T; radar: T }> {
  const byPair = new Map<number, T[]>();
  for (const t of targets) {
    if (!t.matched || t.pairId === 0) continue;
    const list = byPair.get(t.pairId) ?? [];
    list.push(t);
    byPair.set(t.pairId, list);
  }
  const pairs: Array<{ vision: T; radar: T }> = [];
  for (const list of byPair.values()) {
    const vision = list.find((t) => t.vision);
    const radar = list.find((t) => !t.vision);
    if (vision && radar) pairs.push({ vision, radar });
  }
  return pairs;
}

// ---- 状态条数值格式化 ----

/** 后端用 999.0 表示 inf 路沿余量 */
export const EDGE_CLEARANCE_INF = 999.0;

export function fmtEdgeClearance(v: number | undefined): string {
  if (v === undefined || !Number.isFinite(v) || v >= EDGE_CLEARANCE_INF) return "—";
  return `${v.toFixed(2)} m`;
}

/** 预算哨兵同款（999 = 该侧无侧向约束） */
export function fmtBudget(v: number | undefined): string {
  if (v === undefined || !Number.isFinite(v)) return "—";
  if (v >= EDGE_CLEARANCE_INF) return "∞";
  return `${v.toFixed(2)} m`;
}

/** C2 车道归属：-1 左邻 / 0 本道或重叠 / +1 右邻。用于目标悬浮与状态条。 */
export function laneLabel(lane: number | undefined): string {
  if (lane === undefined) return "—";
  if (lane < 0) return "左邻";
  if (lane > 0) return "右邻";
  return "本道";
}
