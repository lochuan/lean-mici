import { describe, expect, it } from "vitest";
import {
  CLS_FILL,
  EDGE_CLEARANCE_INF,
  avoidanceStatus,
  fmtBudget,
  fmtEdgeClearance,
  laneLabel,
  obstacleSide,
  offsetArrow,
  pairMembers,
  targetCls,
} from "../src/lib/avoidance";
import type { RadarViewBox } from "../src/lib/radar";

const VB: RadarViewBox = {
  width: 720,
  height: 540,
  rangeM: 50,
  lateralM: 10,
  padTop: 24,
  padBottom: 56,
  padX: 28,
};

describe("targetCls / CLS_FILL", () => {
  it("maps person red, two-wheelers orange, car blue", () => {
    expect(CLS_FILL[targetCls("person")]).toBe("fill-sl-danger");
    expect(CLS_FILL[targetCls("bicycle")]).toBe("fill-sl-warn");
    expect(CLS_FILL[targetCls("motorcycle")]).toBe("fill-sl-warn");
    expect(CLS_FILL[targetCls("car")]).toBe("fill-sl-info");
  });

  it("falls back to neutral gray for unknown classes and radar points", () => {
    expect(targetCls("truck")).toBe("other");
    expect(targetCls("")).toBe("other");
    expect(CLS_FILL.other).toBe("fill-sl-text-3");
  });
});

describe("avoidanceStatus", () => {
  it("ACTIVE (green) when active and not stale", () => {
    expect(avoidanceStatus({ valid: true, active: true, stale: false })).toEqual({
      label: "ACTIVE",
      badge: "accent",
    });
  });

  it("STANDBY (gray) when valid but not active", () => {
    expect(avoidanceStatus({ valid: true, active: false, stale: false })).toEqual({
      label: "STANDBY",
      badge: "muted",
    });
  });

  it("无数据 (yellow) when stale wins over everything", () => {
    expect(avoidanceStatus({ valid: true, active: true, stale: true }).label).toBe("无数据");
    expect(avoidanceStatus({ stale: true }).badge).toBe("warn");
    expect(avoidanceStatus({ stale: false }).label).toBe("STANDBY");
  });
});

describe("offsetArrow", () => {
  const egoX = 360;

  it("points left when yDes > 0 (向左偏 = screen left, same sign as yRel)", () => {
    const a = offsetArrow(2, VB, egoX);
    expect(a.x2).toBeLessThan(a.x1);
    // 2 m 在 ±10 m 量程里的投影位置
    expect(a.x2).toBeCloseTo(egoX - (2 / 10) * (VB.width / 2 - VB.padX));
  });

  it("points right when yDes < 0", () => {
    expect(offsetArrow(-3, VB, egoX).x2).toBeGreaterThan(egoX);
  });

  it("stays at ego when yDes is 0", () => {
    expect(offsetArrow(0, VB, egoX).x2).toBeCloseTo(egoX);
  });

  it("clamps to ±lateralM", () => {
    const left = offsetArrow(99, VB, egoX);
    expect(left.x2).toBeCloseTo(VB.padX);
    expect(left.yDes).toBe(10);
    const right = offsetArrow(-99, VB, egoX);
    expect(right.x2).toBeCloseTo(VB.width - VB.padX);
  });
});

describe("obstacleSide", () => {
  it("labels the obstacle side only (+1 = obstacle on right)", () => {
    expect(obstacleSide(1)).toBe("障碍在右");
    expect(obstacleSide(-1)).toBe("障碍在左");
    expect(obstacleSide(0)).toBe("—");
    expect(obstacleSide(undefined)).toBe("—");
  });
});

describe("pairMembers", () => {
  const t = (over: { vision: boolean; matched: boolean; pairId: number }) => over;

  it("links matched vision↔radar pairs sharing a pairId", () => {
    const pairs = pairMembers([
      { vision: true, matched: true, pairId: 3 },
      { vision: false, matched: true, pairId: 3 },
      { vision: true, matched: false, pairId: 0 },
    ]);
    expect(pairs).toHaveLength(1);
    expect(pairs[0].vision.vision).toBe(true);
    expect(pairs[0].radar.vision).toBe(false);
  });

  it("ignores unmatched and single-sided entries", () => {
    expect(pairMembers([{ vision: true, matched: true, pairId: 5 }])).toHaveLength(0);
    expect(pairMembers([{ vision: true, matched: false, pairId: 5 }])).toHaveLength(0);
  });
});

describe("fmtEdgeClearance", () => {
  it("formats meters and hides the inf sentinel", () => {
    expect(fmtEdgeClearance(1.25)).toBe("1.25 m");
    expect(fmtEdgeClearance(EDGE_CLEARANCE_INF)).toBe("—");
    expect(fmtEdgeClearance(undefined)).toBe("—");
  });
});

describe("fmtBudget", () => {
  it("formats meters and collapses the unconstrained sentinel", () => {
    expect(fmtBudget(0.9)).toBe("0.90 m");
    expect(fmtBudget(0)).toBe("0.00 m");
    expect(fmtBudget(999.0)).toBe("\u221e"); // 无侧向约束
    expect(fmtBudget(undefined)).toBe("\u2014");
  });
});

describe("laneLabel", () => {
  it("maps C2 lane codes to labels", () => {
    expect(laneLabel(-1)).toBe("\u5de6\u90bb");
    expect(laneLabel(0)).toBe("\u672c\u9053");
    expect(laneLabel(1)).toBe("\u53f3\u90bb");
    expect(laneLabel(undefined)).toBe("\u2014");
  });
});
