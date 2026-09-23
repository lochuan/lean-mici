/** 鸟瞰图投影的纯函数层：坐标投影。
 *
 *  供 AvoidanceView 复用——投影方向（左正右负、前方为正）和钳制行为是
 *  最容易错的地方，vitest 直接锁死。
 */
import type { EagleTarget } from "./schema";

/** 鸟瞰图视口：车头朝上（屏幕上方），ego 位于底部中央 */
export interface RadarViewBox {
  /** svg 用户坐标宽 */
  width: number;
  /** svg 用户坐标高 */
  height: number;
  /** 前向量程（m），dRel 超出会被钳到边缘 */
  rangeM: number;
  /** 横向半量程（m），|yRel| 超出会被钳到边缘 */
  lateralM: number;
  /** 上下留白（svg 单位），给刻度标签和 ego 图标 */
  padTop: number;
  padBottom: number;
  /** 左右留白（svg 单位） */
  padX: number;
}

/** 车辆坐标系 → svg 坐标。
 *
 * dRel：0（车头）在底部，rangeM 在顶部；yRel：左正（openpilot 约定）
 * 映射为屏幕左。绘图区左右各留 padX，圆点不会压在边框上。越界的点
 * 钳到量程边缘——比直接丢弃好，能看到量程外还有东西。
 */
export function projectPoint(p: Pick<EagleTarget, "dRel" | "yRel">, vb: RadarViewBox): { x: number; y: number } {
  const plotLeft = vb.padX;
  const plotRight = vb.width - vb.padX;
  const usableH = vb.height - vb.padTop - vb.padBottom;
  const dRel = clamp(p.dRel, 0, vb.rangeM);
  const yRel = clamp(p.yRel, -vb.lateralM, vb.lateralM);
  const t = (vb.lateralM - yRel) / (2 * vb.lateralM); // 0=最左, 1=最右
  return {
    x: plotLeft + t * (plotRight - plotLeft),
    y: vb.height - vb.padBottom - (dRel / vb.rangeM) * usableH,
  };
}

/** 横向网格线（yRel 米值，从 -lateralM 到 +lateralM）的 x 坐标 */
export function lateralX(yRel: number, vb: RadarViewBox): number {
  return projectPoint({ dRel: 0, yRel }, vb).x;
}

/** 纵向网格线（dRel 米值）的 y 坐标 */
export function rangeY(dRel: number, vb: RadarViewBox): number {
  return projectPoint({ dRel, yRel: 0 }, vb).y;
}

/** 量程刻度线的 dRel 值（含 0 与 rangeM） */
export function rangeTicks(rangeM: number, stepM: number): number[] {
  const ticks: number[] = [];
  for (let d = 0; d < rangeM + 1e-6; d += stepM) ticks.push(d);
  if (ticks[ticks.length - 1] !== rangeM) ticks.push(rangeM);
  return ticks;
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
