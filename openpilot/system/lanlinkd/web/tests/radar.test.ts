import { describe, expect, it } from "vitest";
import type { RadarPoint } from "../src/lib/schema";
import {
  clamp,
  lateralX,
  projectPoint,
  rangeTicks,
  rangeY,
  trackBucket,
  type RadarViewBox,
} from "../src/lib/radar";

const VB: RadarViewBox = {
  width: 720,
  height: 540,
  rangeM: 150,
  lateralM: 10,
  padTop: 24,
  padBottom: 56,
  padX: 28,
};

const pt = (dRel: number, yRel: number, vRel = 0): RadarPoint => ({
  trackId: 0,
  dRel,
  yRel,
  vRel,
});

describe("projectPoint", () => {
  it("puts dRel=0 at the bottom and rangeM at the top", () => {
    const near = projectPoint(pt(0, 0), VB);
    const far = projectPoint(pt(150, 0), VB);
    // 底部留白之下就是 ego，顶部留白给标签
    expect(near.y).toBeCloseTo(VB.height - VB.padBottom);
    expect(far.y).toBeCloseTo(VB.padTop);
    expect(near.y).toBeGreaterThan(far.y);
  });

  it("maps yRel left-positive to screen left", () => {
    // openpilot 约定：yRel 左正。鸟瞰图（车头朝上）里车左 = 屏幕左
    const left = projectPoint(pt(50, +10), VB);
    const right = projectPoint(pt(50, -10), VB);
    const center = projectPoint(pt(50, 0), VB);
    expect(left.x).toBeCloseTo(VB.padX);
    expect(right.x).toBeCloseTo(VB.width - VB.padX);
    expect(center.x).toBeCloseTo(VB.width / 2);
    expect(left.x).toBeLessThan(right.x);
  });

  it("clamps out-of-range points to the edges instead of dropping them", () => {
    const beyond = projectPoint(pt(400, 30), VB);
    expect(beyond.y).toBeCloseTo(VB.padTop);
    expect(beyond.x).toBeCloseTo(VB.padX);
    const behind = projectPoint(pt(-5, -30), VB);
    expect(behind.y).toBeCloseTo(VB.height - VB.padBottom);
    expect(behind.x).toBeCloseTo(VB.width - VB.padX);
  });
});

describe("lateralX / rangeY", () => {
  it("agree with projectPoint on the same axes", () => {
    for (const yRel of [-10, -5, 0, 5, 10]) {
      expect(lateralX(yRel, VB)).toBeCloseTo(projectPoint(pt(0, yRel), VB).x);
    }
    for (const dRel of [0, 30, 75, 150]) {
      expect(rangeY(dRel, VB)).toBeCloseTo(projectPoint(pt(dRel, 0), VB).y);
    }
  });
});

describe("trackBucket", () => {
  it("buckets with a ±0.5 m/s dead band", () => {
    expect(trackBucket(-20)).toBe("approach"); // 对向车：vRel ≈ -2×vEgo
    expect(trackBucket(-0.6)).toBe("approach");
    expect(trackBucket(-0.4)).toBe("static");
    expect(trackBucket(0)).toBe("static");
    expect(trackBucket(0.4)).toBe("static");
    expect(trackBucket(0.6)).toBe("recede");
    expect(trackBucket(5)).toBe("recede");
  });
});

describe("rangeTicks", () => {
  it("always includes both 0 and rangeM", () => {
    expect(rangeTicks(150, 30)).toEqual([0, 30, 60, 90, 120, 150]);
    // 步长不整除时也要收尾到 rangeM
    expect(rangeTicks(100, 30)).toEqual([0, 30, 60, 90, 100]);
  });
});

describe("clamp", () => {
  it("never escapes", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(11, 0, 10)).toBe(10);
  });
});
