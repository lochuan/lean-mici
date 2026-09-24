/** lib/lanes.ts 车道视图纯函数：路径生成 / 填充多边形 / 目标形状 / 融合位置。 */
import { describe, expect, it } from "vitest";

import {
  egoCarRect,
  fusedTargets,
  laneFillD,
  lanePathD,
  outerLineOpacity,
  shapePx,
  targetDisplayPosition,
  targetShape,
  VISUAL_LENGTH_FACTOR,
} from "@/lib/lanes";
import { lateralX, rangeY, type RadarViewBox } from "@/lib/radar";
import type { EagleTarget, LaneLineSnap } from "@/lib/schema";

const VB = {
  width: 720,
  height: 540,
  rangeM: 60,
  lateralM: 8,
  padTop: 24,
  padBottom: 64,
  padX: 28,
} satisfies RadarViewBox;

const GRID = Array.from({ length: 13 }, (_, i) => i * 5);

const line = (ys: number[], prob?: number): LaneLineSnap => ({ y: ys, prob, std: 0.1 });

const tgt = (over: Partial<EagleTarget>): EagleTarget => ({
  dRel: 20,
  yRel: -1,
  vRel: 0,
  cls: "",
  conf: 0,
  weight: 0,
  matched: false,
  inGate: false,
  vision: false,
  pairId: 0,
  lane: 0,
  ...over,
});

describe("lanePathD", () => {
  it("builds a polyline through the grid with left-positive y on screen-left", () => {
    const d = lanePathD(line(new Array(13).fill(1.75)), GRID, VB)!;
    expect(d.startsWith("M")).toBe(true);
    expect(d.match(/L/g)?.length).toBe(12); // 13 points
    // y=+1.75（左正）应投影到屏幕左半（x < 中线）
    const pts = d.slice(1).split("L");
    const first = pts[0].split(",").map(Number);
    expect(first[0]).toBe(lateralX(1.75, VB));
    expect(first[0]).toBeLessThan(VB.width / 2);
    expect(first[1]).toBe(rangeY(0, VB));
  });

  it("returns null for a line without data", () => {
    expect(lanePathD(null, GRID, VB)).toBeNull();
    expect(lanePathD({ y: null, prob: 0.5 }, GRID, VB)).toBeNull();
  });
});

describe("laneFillD", () => {
  it("makes a closed polygon between two boundaries", () => {
    const d = laneFillD(line(new Array(13).fill(1.75)), line(new Array(13).fill(-1.75)), GRID, VB)!;
    expect(d.endsWith("Z")).toBe(true);
    // 13 前向 + 13 回程 = 26 个点
    expect(d.match(/L/g)?.length).toBe(25);
  });

  it("returns null when either boundary is missing", () => {
    expect(laneFillD(null, line(new Array(13).fill(1)), GRID, VB)).toBeNull();
    expect(laneFillD(line(new Array(13).fill(1)), { y: null, prob: 0.5 }, GRID, VB)).toBeNull();
  });
});

describe("targetShape", () => {
  it("scales by class", () => {
    expect(targetShape("car")).toEqual({ kind: "rect", wM: 1.8, hM: 4.5 });
    expect(targetShape("truck")).toEqual({ kind: "rect", wM: 2.6, hM: 8 });
    expect(targetShape("bus")).toEqual({ kind: "rect", wM: 2.6, hM: 8 });
    expect(targetShape("person")).toEqual({ kind: "circle", wM: 0.7, hM: 0.7 });
    expect(targetShape("bicycle")).toEqual({ kind: "rect", wM: 0.8, hM: 2 });
    expect(targetShape("motorcycle")).toEqual({ kind: "rect", wM: 0.8, hM: 2 });
    expect(targetShape("tricycle")).toEqual({ kind: "rect", wM: 1.2, hM: 2.5 });
    expect(targetShape("")).toEqual({ kind: "rect", wM: 1, hM: 3 }); // 未知类兜底
  });
});

describe("fusedTargets + targetDisplayPosition", () => {
  it("drops radar-only points and pairs keep vision class with radar position", () => {
    const radar = tgt({ dRel: 22, yRel: -1.4, pairId: 1 });
    const vision = tgt({ dRel: 20, yRel: -1, vision: true, matched: true, pairId: 1, cls: "person" });
    const lonely = tgt({ vision: true, cls: "car" });
    const all = [radar, vision, lonely];
    expect(fusedTargets(all)).toHaveLength(2); // 雷达点不画
    const pos = targetDisplayPosition(vision, all);
    expect(pos).toEqual({ dRel: 22, yRel: -1.4 }); // 配对目标用雷达测距
    expect(targetDisplayPosition(lonely, all)).toEqual({ dRel: 20, yRel: -1 }); // 未配对用视觉投影
  });
});

describe("outerLineOpacity", () => {
  it("draws outer lines by prob, hides low-confidence/unknown", () => {
    expect(outerLineOpacity(line(new Array(13).fill(0), 0.9))).toBeGreaterThan(0.3);
    expect(outerLineOpacity(line(new Array(13).fill(0), 0.2))).toBeNull();
    expect(outerLineOpacity(line(new Array(13).fill(0)))).toBeNull();
    expect(outerLineOpacity(null)).toBeNull();
  });
});

describe("shapePx / egoCarRect", () => {
  it("width is metric-horizontal; length gets a visual elongation factor", () => {
    const car = shapePx(targetShape("car"), VB);
    // 横向真实比例：1.8m * (664px / 16m) = 74.7px
    expect(car.wPx).toBeCloseTo((1.8 / (2 * VB.lateralM)) * (VB.width - 2 * VB.padX), 5);
    // 纵向 = 度量 × 拉长系数，封顶 2.2 倍宽（避免大车占满画布）
    const sy = (VB.height - VB.padTop - VB.padBottom) / VB.rangeM;
    const sx = (VB.width - 2 * VB.padX) / (2 * VB.lateralM);
    const expectH = (wM: number, hM: number): number =>
      Math.min(hM * sy * VISUAL_LENGTH_FACTOR, (wM * sx) * 2.2);
    expect(car.hPx).toBeCloseTo(expectH(1.8, 4.5), 5);
    const person = shapePx(targetShape("person"), VB);
    expect(person.hPx).toBe(person.wPx); // 圆
    const truck = shapePx(targetShape("truck"), VB);
    expect(truck.hPx).toBeCloseTo(expectH(2.6, 8), 5);
  });

  it("ego car is a car-shaped rect at bottom center", () => {
    const r = egoCarRect(VB);
    const car = shapePx(targetShape("car"), VB);
    expect(r.wPx).toBe(car.wPx);
    expect(r.hPx).toBe(car.hPx);
    expect(r.cx).toBe(lateralX(0, VB));
    // 车尾贴底：中心在 0m 刻度线之下
    expect(r.cy).toBeGreaterThan(rangeY(0, VB));
  });
});
