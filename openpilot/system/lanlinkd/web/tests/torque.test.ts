import { describe, expect, it } from "vitest";
import { binRows, speedDepHint, torqueHeadline } from "../src/lib/torque";
import type { TorqueLearned, TorqueStatus } from "../src/lib/schema";

function learned(over: Partial<TorqueLearned> = {}): TorqueLearned {
  return {
    valid: true, useParams: true, latAccelFactor: 2.5, friction: 0.1, latAccelOffset: 0,
    latAccelFactorRaw: 2.5, frictionRaw: 0.1, calPerc: 100, totalPoints: 4000, decay: 50, resets: 1,
    bins: [], ...over,
  };
}

function status(over: Partial<TorqueStatus> = {}): TorqueStatus {
  return {
    source: "live",
    learned: learned(),
    offline: { lateralControl: "torque", latAccelFactor: 2.5, friction: 0.1 },
    toggles: { EnforceTorqueControl: true, LiveTorqueParamsToggle: true, SpeedDependentTorqueToggle: false },
    ...over,
  };
}

describe("torqueHeadline", () => {
  it("non-torque cars are not applicable", () => {
    expect(torqueHeadline(status({ offline: { lateralControl: "angle", latAccelFactor: null, friction: null } })).tone).toBe("muted");
  });

  it("useParams off means stock values are used", () => {
    expect(torqueHeadline(status({ learned: learned({ useParams: false }) })).text).toBe("未启用（使用出厂值）");
  });

  it("valid learned params are active, invalid shows progress", () => {
    expect(torqueHeadline(status()).tone).toBe("accent");
    expect(torqueHeadline(status({ learned: learned({ valid: false, calPerc: 42 }) }))).toEqual({ text: "学习中 42%", tone: "warn" });
  });

  it("no data", () => {
    expect(torqueHeadline(status({ source: "none", learned: null })).text).toBe("暂无学习数据");
  });
});

describe("speedDepHint", () => {
  const bin = { center: 10, lo: 8, hi: 12, latAccelFactor: 2.5, friction: 0.1, valid: false, calPerc: 10 };

  it("toggle on without bins waits for a drive", () => {
    const t = status({ toggles: { EnforceTorqueControl: true, LiveTorqueParamsToggle: true, SpeedDependentTorqueToggle: true } });
    expect(speedDepHint(t)).toContain("下次行驶");
  });

  it("toggle off with cached bins says they are not in effect", () => {
    expect(speedDepHint(status({ learned: learned({ bins: [bin] }) }))).toContain("不生效");
  });

  it("consistent state has no hint", () => {
    expect(speedDepHint(status())).toBeNull();
  });
});

describe("binRows", () => {
  it("formats km/h range, delta vs global and state", () => {
    const rows = binRows([
      { center: 6.5, lo: 5, hi: 8, latAccelFactor: 2.0, friction: 0.12, valid: true, calPerc: 100 },
      { center: 10, lo: 8, hi: 12, latAccelFactor: 2.75, friction: 0.1, valid: false, calPerc: 37 },
      { center: 15, lo: 12, hi: 18, latAccelFactor: 2.5, friction: 0.1, valid: false, calPerc: null },
    ], 2.5);
    expect(rows[0]).toMatchObject({ range: "18–29", latAccelFactor: "2.000", delta: "-20%", progress: 100, state: "已生效", tone: "accent" });
    expect(rows[1]).toMatchObject({ range: "29–43", delta: "+10%", progress: 37, state: "学习中 37%", tone: "warn" });
    expect(rows[2]).toMatchObject({ delta: "0%", progress: null, state: "学习中" });
  });

  it("no delta when global LAF is unknown", () => {
    expect(binRows([{ center: 10, lo: 8, hi: 12, latAccelFactor: 2, friction: 0.1, valid: true, calPerc: 100 }], 0)[0].delta).toBe("");
  });
});
