import { describe, expect, it } from "vitest";
import { decimalsOf, nextValue, quantize } from "../src/lib/numeric";
import { formatSliderValue, resolveUnit } from "../src/lib/utils";

describe("quantize", () => {
  it("kills float drift on fractional steps", () => {
    // TorqueParamsOverrideLatAccelFactor: min 0.1 max 5.0 step 0.1
    // 朴素加法会得到 0.30000000000000004，直接显示给用户
    let v = 0.1;
    for (let i = 0; i < 9; i += 1) v = nextValue(v, 1, 0.1, 5.0, 0.1);
    expect(v).toBe(1);
    expect(String(v)).not.toContain("0000");
  });

  it("never escapes the range", () => {
    expect(quantize(999, 0.1, 5.0, 0.1)).toBe(5);
    expect(quantize(-999, 0.1, 5.0, 0.1)).toBe(0.1);
    // 负数下限也要正确（SpeedLimitValueOffset: -30..30）
    expect(quantize(-999, -30, 30, 1)).toBe(-30);
    expect(quantize(999, -30, 30, 1)).toBe(30);
  });

  it("snaps to the step grid relative to min, not to zero", () => {
    // min=0.1 step=0.1 时，网格是 0.1/0.2/0.3...，不是 0/0.1/0.2
    expect(quantize(0.24, 0.1, 5.0, 0.1)).toBe(0.2);
    expect(quantize(0.26, 0.1, 5.0, 0.1)).toBe(0.3);
    // BlinkerMinLateralControlSpeed: 0..255 step 5
    expect(quantize(37, 0, 255, 5)).toBe(35);
    expect(quantize(38, 0, 255, 5)).toBe(40);
  });

  it("stops exactly at the bounds when the step does not divide the span", () => {
    // 0..255 step 5：255 不是 5 的倍数距离 0 的整数倍？(255/5=51，是)
    // 用一个真不整除的情形确认不会越界
    expect(nextValue(253, 1, 0, 255, 5)).toBe(255);
    expect(nextValue(255, 1, 0, 255, 5)).toBe(255);
    expect(nextValue(0, -1, 0, 255, 5)).toBe(0);
  });

  it("counts decimals from the step", () => {
    expect(decimalsOf(1)).toBe(0);
    expect(decimalsOf(0.1)).toBe(1);
    expect(decimalsOf(0.05)).toBe(2);
  });
});

describe("formatSliderValue", () => {
  it("shows as many decimals as the step implies", () => {
    expect(formatSliderValue(3, 1)).toBe("3");
    expect(formatSliderValue(0.3, 0.1)).toBe("0.3");
    // 小数步长下整数值也带小数位："1.0" 而不是 "1"，这样
    // 1.0 → 1.1 → 1.2 连续调节时数字宽度不跳动
    expect(formatSliderValue(1, 0.1)).toBe("1.0");
    expect(formatSliderValue(2, 0.05)).toBe("2.00");
  });

  it("does not leak float noise", () => {
    expect(formatSliderValue(0.30000000000000004, 0.1)).toBe("0.3");
  });
});

describe("resolveUnit", () => {
  it("handles the plain-string form", () => {
    expect(resolveUnit("m/s²", true)).toBe("m/s²");
  });

  it("localizes the English word units, which read badly next to a number", () => {
    // schema 里只有这两个是英文单词，其余都是符号；"0second" 很别扭
    expect(resolveUnit("second", true)).toBe("秒");
    expect(resolveUnit("meters", true)).toBe("米");
  });

  it("follows IsMetric for the split form", () => {
    // SpeedLimitValueOffset 的 unit 是 {metric, imperial}
    const u = { metric: "km/h", imperial: "mph" };
    expect(resolveUnit(u, true)).toBe("km/h");
    expect(resolveUnit(u, false)).toBe("mph");
  });

  it("passes through anything it does not recognize", () => {
    // 上游新增单位时应原样显示，而不是变成空白
    expect(resolveUnit("furlongs", true)).toBe("furlongs");
  });

  it("is blank when absent", () => {
    expect(resolveUnit(undefined, true)).toBe("");
  });
});
