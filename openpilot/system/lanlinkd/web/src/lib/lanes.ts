/** 车道视图纯函数层：modelV2 车道几何快照 → SVG 路径/形状。
 *
 *  与 radar.ts / avoidance.ts 同款分工：可测的几何与判定逻辑全在这里，
 *  AvoidanceView.vue 只做渲染。坐标约定与后端 lanes.py 一致：
 *  y 左正（雷达系）、dRel 保险杠原点、x 网格 0-60m（LANE_GRID_X）。
 */
import type { EagleTarget, LaneLineSnap, LaneSnapshot } from "./schema";
import { lateralX, rangeY, type RadarViewBox } from "./radar";

/** 本车道边界索引（lanes.py 同款 openpilot 惯例） */
export const LIDX_OUTER_LEFT = 0;
export const LIDX_LEFT = 1;
export const LIDX_RIGHT = 2;
export const LIDX_OUTER_RIGHT = 3;

/** 网格 x（m），与后端 LANE_GRID_X 对齐 */
export const LANE_GRID_X = Array.from({ length: 13 }, (_, i) => i * 5);

/** 类别 → 显示形状（w=横向 m，h=纵向 m）。尺寸取 eagled 半宽表的两倍宽。 */
export interface TargetShape {
  kind: "rect" | "circle";
  wM: number;
  hM: number;
}
const RECT = (wM: number, hM: number): TargetShape => ({ kind: "rect", wM, hM });

export function targetShape(cls: string): TargetShape {
  switch (cls) {
    case "car":
      return RECT(1.8, 4.5);
    case "truck":
    case "bus":
      return RECT(2.6, 8);
    case "person":
      return { kind: "circle", wM: 0.7, hM: 0.7 };
    case "bicycle":
    case "motorcycle":
      return RECT(0.8, 2);
    case "tricycle":
      return RECT(1.2, 2.5);
    default:
      return RECT(1, 3);
  }
}

/** 只画融合对象：vision=true 的目标（雷达点按设计移除——未配对的雷达点
 *  没有类别与可靠横向位置，配上对的已由视觉行代表） */
export function fusedTargets(targets: EagleTarget[]): EagleTarget[] {
  return targets.filter((t) => t.vision);
}

/** 配对目标的显示位置：类别来自视觉，测距用雷达（planner 实际消费的值）。 */
export function targetDisplayPosition(t: EagleTarget, targets: EagleTarget[]): { dRel: number; yRel: number } {
  if (t.vision && t.matched && t.pairId > 0) {
    const twin = targets.find((o) => !o.vision && o.pairId === t.pairId);
    if (twin) return { dRel: twin.dRel, yRel: twin.yRel };
  }
  return { dRel: t.dRel, yRel: t.yRel };
}

/** 外侧车道线透明度：prob<0.3 或未知不画（null）。 */
export function outerLineOpacity(line: LaneLineSnap | null): number | null {
  const prob = line?.prob;
  if (prob === undefined || prob === null || prob < 0.3) return null;
  return Math.min(0.6, 0.15 + 0.45 * prob);
}

/** 一条线（车道线/路沿/路径）→ SVG polyline path；无数据返回 null。 */
export function lanePathD(line: Pick<LaneLineSnap, "y"> | null, gridX: number[], vb: RadarViewBox): string | null {
  if (!line?.y || line.y.length === 0) return null;
  const pts = line.y.map((y, i) => {
    const d = gridX[i] ?? i * 5;
    return `${lateralX(y, vb)},${rangeY(d, vb)}`;
  });
  return `M${pts.join("L")}`;
}

/** 两条边界之间填充成闭合多边形（本车道/邻道底色）；任一侧无数据为 null。 */
export function laneFillD(
  a: LaneLineSnap | null,
  b: LaneLineSnap | null,
  gridX: number[],
  vb: RadarViewBox,
): string | null {
  if (!a?.y?.length || !b?.y?.length) return null;
  const forward = a.y.map((y, i) => `${lateralX(y, vb)},${rangeY(gridX[i] ?? i * 5, vb)}`);
  const backward = b.y
    .map((y, i) => ({ y, x: gridX[i] ?? i * 5 }))
    .reverse()
    .map(({ y, x }) => `${lateralX(y, vb)},${rangeY(x, vb)}`);
  return `M${[...forward, ...backward].join("L")}Z`;
}

/** 本车俯视几何（车形，底部中央，车头朝上）。 */
export function egoCarRect(vb: RadarViewBox): { cx: number; cy: number; wPx: number; hPx: number } {
  const { wPx, hPx } = shapePx(targetShape("car"), vb);
  // 车尾贴 svg 底缘，车头朝上：中心在 rangeY(0) 之下半个车长
  return { cx: lateralX(0, vb), cy: rangeY(0, vb) + hPx / 2, wPx, hPx };
}

/** 各向异性投影（横向 41px/m vs 纵向 7.5px/m）下，度量纵向长度会压成
 *  纸片。目标/本车的纵向显示统一乘这个视觉拉长系数，并封顶 2.2 倍宽，
 *  保证"车是长的"这一观感且大车不占满画布。横向始终真实比例。 */
export const VISUAL_LENGTH_FACTOR = 2.5;

function pxPerMeter(vb: RadarViewBox): { x: number; y: number } {
  return {
    x: (vb.width - 2 * vb.padX) / (2 * vb.lateralM),
    y: (vb.height - vb.padTop - vb.padBottom) / vb.rangeM,
  };
}

/** 类别形状 → 像素尺寸（横向真实比例，纵向拉长并封顶）。 */
export function shapePx(shape: TargetShape, vb: RadarViewBox): { kind: "rect" | "circle"; wPx: number; hPx: number } {
  const { x: sx, y: sy } = pxPerMeter(vb);
  const wPx = shape.wM * sx;
  const hPx = shape.kind === "circle"
    ? wPx
    : Math.min(shape.hM * sy * VISUAL_LENGTH_FACTOR, wPx * 2.2);
  return { kind: shape.kind, wPx, hPx };
}

/** lanes 快照存在且至少本车道一条边界可用时才认为有车道层可画。 */
export function hasLaneLayer(lanes: LaneSnapshot | null | undefined): lanes is LaneSnapshot {
  if (!lanes?.laneLines) return false;
  const l = lanes.laneLines[LIDX_LEFT];
  const r = lanes.laneLines[LIDX_RIGHT];
  return Boolean(l?.y?.length || r?.y?.length);
}
