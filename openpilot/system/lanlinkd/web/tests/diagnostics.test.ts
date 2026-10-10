import { describe, expect, it } from "vitest";
import {
  cameraView, driftChecklist, driftView, driveText, fmtDuration, steeringView, suggestCameraOffset,
  toneColor, torqueOffsetTiltDeg, traceSegments, weaveView,
} from "../src/lib/diagnostics";
import type { CameraDiag, DriftDiag, SteeringDiag, WeaveDiag } from "../src/lib/schema";

function cam(over: Partial<CameraDiag> = {}): CameraDiag {
  return {
    source: "live", calStatus: "calibrated", calPerc: 100, pitchDeg: 0, yawDeg: 0,
    limits: { pitchUpDeg: 5.2, pitchDownDeg: 9.7, yawDeg: 3.96 }, cameraOffsetM: 0, ...over,
  };
}

function drift(over: Partial<DriftDiag> = {}): DriftDiag {
  return {
    seconds: 120, carOffsetM: 0.02, planOffsetM: 0.01, angleOffsetDeg: 0.5, angleOffsetValid: true,
    accurateAngle: "ready", latAccelOffset: 0, ...over,
  };
}

function weave(over: Partial<WeaveDiag> = {}): WeaveDiag {
  return {
    seconds: 300, windows: 30, weaveWindows: 0, medianP2pM: 0.1, medianPeriodS: 4,
    trace: [], lateralDelayS: 0.2, factoryDelayS: 0.12, ...over,
  };
}

function steering(over: Partial<SteeringDiag> = {}): SteeringDiag {
  return {
    control: "torque", activeSeconds: 600, curveSeconds: 100, saturatedCurveSeconds: 0,
    usage: [300, 200, 50, 30, 20], epsTempFaults: 0, epsPermanent: false, maxLatAccel: 2.1, ...over,
  };
}

describe("driveText", () => {
  it("driving, parked with last drive, never driven", () => {
    expect(driveText({ started: true, seconds: 754 })).toBe("本次行驶已 12 分钟");
    expect(driveText({ started: false, seconds: 900 })).toBe("未在行驶，显示上次行驶的数据");
    expect(driveText({ started: false, seconds: 0 })).toBe("还没有行驶数据");
  });
});

describe("cameraView", () => {
  it("no calibration at all", () => {
    expect(cameraView(null).tone).toBe("muted");
  });

  it("good when every axis is under half of its limit", () => {
    const v = cameraView(cam({ yawDeg: 1.9, pitchDeg: -2.5 }));
    expect(v).toMatchObject({ tone: "accent", badge: "良好", adjust: null });
  });

  it("usable but suggest adjusting between 0.5 and 0.8 of the limit", () => {
    expect(cameraView(cam({ yawDeg: 2.4 }))).toMatchObject({ tone: "warn", badge: "建议调整" });
  });

  it("needs adjusting at 0.8 of the limit or when calibration is invalid", () => {
    expect(cameraView(cam({ pitchDeg: 8 })).tone).toBe("danger");
    expect(cameraView(cam({ calStatus: "invalid", yawDeg: 5 }))).toMatchObject({ tone: "danger", badge: "需调整" });
  });

  it("pitch uses the up limit when facing up and the down limit when facing down", () => {
    // 4° 朝下：4/9.7 < 0.5 良好；4° 朝上：4/5.2 ≥ 0.5 建议调整
    expect(cameraView(cam({ pitchDeg: 4 })).tone).toBe("accent");
    expect(cameraView(cam({ pitchDeg: -4 })).tone).toBe("warn");
  });

  it("shows progress while calibrating", () => {
    const v = cameraView(cam({ calStatus: "uncalibrated", calPerc: 42 }));
    expect(v).toMatchObject({ tone: "muted", progress: 42 });
    expect(v.badge).toContain("42%");
    expect(cameraView(cam({ calStatus: "recalibrating", calPerc: 10 })).progress).toBe(10);
    expect(cameraView(cam()).progress).toBeNull();
  });

  it("device facing left and down → turn right and tilt up", () => {
    const v = cameraView(cam({ yawDeg: 2.5, pitchDeg: 6 }));
    expect(v.adjust).toBe("往右转约 2.5°、往上抬约 6°");
    // 瞄准点：朝左 → 左（x<0），朝下 → 下（y>0）
    expect(v.aim).toEqual({ x: -2.5, y: 6 });
  });

  it("device facing right and up → turn left and tilt down; axes inside the ideal zone are not mentioned", () => {
    expect(cameraView(cam({ yawDeg: -3.5, pitchDeg: -1 })).adjust).toBe("往左转约 3.5°");
    expect(cameraView(cam({ yawDeg: 0.3, pitchDeg: -4.44 })).adjust).toBe("往下压约 4.4°");
  });
});

describe("suggestCameraOffset", () => {
  it("offset = (right − left) / 2, >0 means device is left of center", () => {
    const s = suggestCameraOffset(60, 80)!;
    expect(s.offsetM).toBeCloseTo(0.1);
    expect(s.clamped).toBe(false);
    expect(s.side).toBe("设备在中线左侧 10 cm");
    expect(suggestCameraOffset(75, 65)!.offsetM).toBeCloseTo(-0.05);
    expect(suggestCameraOffset(75, 65)!.side).toBe("设备在中线右侧 5 cm");
    expect(suggestCameraOffset(70, 70)!.side).toBe("设备就在中线上");
  });

  it("rounds to the 1 cm step of the param", () => {
    expect(suggestCameraOffset(60, 63.3)!.offsetM).toBeCloseTo(0.02);
  });

  it("clamps to ±0.35 m and explains", () => {
    const s = suggestCameraOffset(20, 120)!;
    expect(s.offsetM).toBeCloseTo(0.35);
    expect(s.clamped).toBe(true);
    expect(s.hint).not.toBeNull();
    expect(suggestCameraOffset(120, 20)!.offsetM).toBeCloseTo(-0.35);
  });

  it("needs two positive numbers", () => {
    expect(suggestCameraOffset(null, 70)).toBeNull();
    expect(suggestCameraOffset(0, 70)).toBeNull();
    expect(suggestCameraOffset(Number.NaN, 70)).toBeNull();
  });
});

describe("driftView", () => {
  it("not enough straight-road samples shows progress", () => {
    const v = driftView(drift({ seconds: 15, carOffsetM: 0.3 }));
    expect(v).toMatchObject({ tone: "muted", cause: null });
    expect(v.progress?.pct).toBe(25);
    expect(v.progress?.text).toContain("45 秒");
    expect(driftView(drift({ carOffsetM: null })).cause).toBeNull();
  });

  it("centered under 10 cm hints at camera mounting", () => {
    const v = driftView(drift({ carOffsetM: -0.06 }));
    expect(v).toMatchObject({ tone: "accent", cause: "centered" });
    expect(v.advice).toContain("量一量");
  });

  it("plan on the same side and ≥60% of the offset → the model wants to drift", () => {
    const v = driftView(drift({ carOffsetM: 0.18, planOffsetM: 0.12 }));
    expect(v).toMatchObject({ tone: "warn", cause: "model", headline: "平均偏右 18 cm" });
    expect(v.advice).toContain("相机");
  });

  it("plan near center → control did not follow", () => {
    expect(driftView(drift({ carOffsetM: -0.2, planOffsetM: -0.05 }))).toMatchObject({
      cause: "control", headline: "平均偏左 20 cm",
    });
    expect(driftView(drift({ carOffsetM: 0.2, planOffsetM: -0.2 })).cause).toBe("control");
    expect(driftView(drift({ carOffsetM: 0.2, planOffsetM: null })).cause).toBe("control");
  });
});

describe("driftChecklist", () => {
  const byId = (d: DriftDiag) => Object.fromEntries(driftChecklist(d).map((c) => [c.id, c]));

  it("all fine", () => {
    expect(driftChecklist(drift()).map((c) => c.state)).toEqual(["ok", "ok", "ok"]);
  });

  it("steering wheel zero: <2° ok, 2–5° warn, invalid bad, missing unknown", () => {
    expect(byId(drift({ angleOffsetDeg: -1.9 })).zero.state).toBe("ok");
    expect(byId(drift({ angleOffsetDeg: 3 })).zero.state).toBe("warn");
    expect(byId(drift({ angleOffsetDeg: 3 })).zero.text).toContain("四轮定位");
    expect(byId(drift({ angleOffsetDeg: 6 })).zero.state).toBe("bad");
    expect(byId(drift({ angleOffsetValid: false })).zero.state).toBe("bad");
    expect(byId(drift({ angleOffsetDeg: null, angleOffsetValid: null })).zero.state).toBe("unknown");
  });

  it("Toyota accurate angle: hidden for other brands", () => {
    expect(byId(drift({ accurateAngle: "pending" })).accurateAngle.state).toBe("warn");
    expect(byId(drift({ accurateAngle: "unknown" })).accurateAngle.state).toBe("unknown");
    expect(byId(drift({ accurateAngle: "n/a" })).accurateAngle).toBeUndefined();
  });

  it("torque offset as an equivalent device tilt, warns above 1°", () => {
    expect(torqueOffsetTiltDeg(0.1712)).toBeCloseTo(1.0, 1);
    expect(byId(drift({ latAccelOffset: 0.1 })).torqueOffset.state).toBe("ok");
    const warn = byId(drift({ latAccelOffset: -0.3 })).torqueOffset;
    expect(warn.state).toBe("warn");
    expect(warn.text).toContain("1.8°");
    expect(byId(drift({ latAccelOffset: null })).torqueOffset.state).toBe("unknown");
  });
});

describe("weaveView", () => {
  it("not enough data", () => {
    expect(weaveView(weave({ seconds: 30, windows: 3 })).tone).toBe("muted");
    expect(weaveView(weave({ windows: 0 })).progress).not.toBeNull();
  });

  it("judges by evaluated 10 s windows, not by total straight seconds", () => {
    // 直道都是几秒长的短段：样本够 90 秒，但连续 10 秒的窗口只有 2 个 → 仍在收集，进度按窗口算
    const v = weaveView(weave({ seconds: 90, windows: 2, weaveWindows: 0 }));
    expect(v.tone).toBe("muted");
    expect(v.progress?.pct).toBe(33);
    expect(v.progress?.text).toContain("连续");
    expect(weaveView(weave({ seconds: 60, windows: 6, weaveWindows: 0 })).tone).toBe("accent");
  });

  it("stable / slight / obvious by share of weaving windows", () => {
    expect(weaveView(weave({ weaveWindows: 1, windows: 30 }))).toMatchObject({ tone: "accent", badge: "稳定" });
    expect(weaveView(weave({ weaveWindows: 3, windows: 30 }))).toMatchObject({ tone: "warn", badge: "轻微摆动" });
    expect(weaveView(weave({ weaveWindows: 6, windows: 30 }))).toMatchObject({ tone: "danger", badge: "明显画龙" });
  });

  it("exposes share, amplitude in cm and period", () => {
    expect(weaveView(weave({ weaveWindows: 6, windows: 30, medianP2pM: 0.346, medianPeriodS: 3.24 }))).toMatchObject({
      weavePct: 20, amplitudeCm: 35, periodS: 3.2,
    });
    expect(weaveView(weave({ medianP2pM: null, medianPeriodS: null }))).toMatchObject({ amplitudeCm: null, periodS: null });
  });
});

describe("traceSegments", () => {
  it("breaks the line at nulls and keeps indices", () => {
    expect(traceSegments([0.1, 0.2, null, null, -0.1, 0, null])).toEqual([
      [{ i: 0, v: 0.1 }, { i: 1, v: 0.2 }],
      [{ i: 4, v: -0.1 }, { i: 5, v: 0 }],
    ]);
    expect(traceSegments([])).toEqual([]);
  });
});

describe("steeringView", () => {
  it("not enough curve time", () => {
    const v = steeringView(steering({ curveSeconds: 12, saturatedCurveSeconds: 5 }));
    expect(v).toMatchObject({ tone: "muted", saturatedPct: null });
    expect(v.progress?.pct).toBe(40);
  });

  it("enough / sometimes short / often short", () => {
    expect(steeringView(steering({ saturatedCurveSeconds: 4 }))).toMatchObject({ tone: "accent", saturatedPct: 4 });
    expect(steeringView(steering({ saturatedCurveSeconds: 12 })).tone).toBe("warn");
    expect(steeringView(steering({ saturatedCurveSeconds: 20 })).tone).toBe("warn");
    expect(steeringView(steering({ saturatedCurveSeconds: 25 })).tone).toBe("danger");
  });

  it("EPS temporary faults are counted, permanent fault overrides everything", () => {
    expect(steeringView(steering()).eps).toMatchObject({ count: 0, tone: "accent" });
    expect(steeringView(steering({ epsTempFaults: 3 })).eps).toMatchObject({ count: 3, tone: "warn" });
    const v = steeringView(steering({ epsPermanent: true }));
    expect(v.tone).toBe("danger");
    expect(v.eps.tone).toBe("danger");
  });

  it("usage bars as share of time; none for angle control", () => {
    const bars = steeringView(steering({ usage: [50, 25, 25, 0, 0] })).usage!;
    expect(bars.map((b) => b.label)).toEqual(["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"]);
    expect(bars.map((b) => b.pct)).toEqual([50, 25, 25, 0, 0]);
    expect(steeringView(steering({ control: "angle", usage: null })).usage).toBeNull();
    expect(steeringView(steering({ usage: [0, 0, 0, 0, 0] })).usage!.every((b) => b.pct === 0)).toBe(true);
  });
});

describe("display helpers", () => {
  it("fmtDuration", () => {
    expect(fmtDuration(44.6)).toBe("45 秒");
    expect(fmtDuration(723)).toBe("12 分 3 秒");
    expect(fmtDuration(3900)).toBe("1 小时 5 分");
  });

  it("toneColor maps to theme variables", () => {
    expect(toneColor("accent")).toBe("var(--color-sl-accent)");
    expect(toneColor("muted")).toBe("var(--color-sl-text-3)");
  });
});
