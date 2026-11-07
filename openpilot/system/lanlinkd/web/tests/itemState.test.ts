import { describe, expect, it } from "vitest";
import { itemState, optionDisabled, resolveTitle, subPanelOpenable } from "../src/lib/itemState";
import type { Item } from "../src/lib/schema";

const ctx = (params: Record<string, string> = {}, caps: Record<string, unknown> = {}) => ({
  params,
  caps: caps as Record<string, string | number | boolean>,
});

const toggle = (over: Partial<Item> = {}): Item => ({
  key: "Mads",
  widget: "toggle",
  title: "启用 MADS",
  ...over,
});

describe("visibility vs. disabled", () => {
  it("hides only when visibility fails", () => {
    // visibility 为假 = 此车型没有这个概念，渲染出来纯属噪音
    const item = toggle({ visibility: [{ type: "capability", field: "brand", equals: "tesla" }] });
    expect(itemState(item, ctx({}, { brand: "toyota" })).visible).toBe(false);
    expect(itemState(item, ctx({}, { brand: "tesla" })).visible).toBe(true);
  });

  it("greys out but keeps showing when enablement fails", () => {
    // 关键行为：不可用要留在页面上并解释原因，否则用户以为功能消失了
    const item = toggle({
      enablement: [{ type: "param", key: "ShowAdvancedControls", equals: true }],
    });
    const s = itemState(item, ctx({}));
    expect(s.visible).toBe(true);
    expect(s.disabled).toBe(true);
    expect(s.reasons.length).toBeGreaterThan(0);
  });
});

describe("schema flags become badges", () => {
  it("marks a param the firmware does not have", () => {
    // 后端注入的是下划线前缀 _missing（settings.py），写错就静默失效
    const s = itemState(toggle({ _missing: true }), ctx({}));
    expect(s.disabled).toBe(true);
    expect(s.badges.some((b) => b.text === "不可用")).toBe(true);
    expect(s.reasons.join()).toContain("固件");
  });

  it("marks blocked params as device-only", () => {
    // AdbEnabled / SshEnabled：后端返回 403，UI 得提前说清楚
    const s = itemState(toggle({ key: "SshEnabled", blocked: true }), ctx({}));
    expect(s.disabled).toBe(true);
    expect(s.badges.some((b) => b.kind === "danger" && b.text === "仅设备端")).toBe(true);
  });

  it("flags needs_onroad_cycle without disabling", () => {
    // 能改，只是生效要等一次上下电
    const s = itemState(toggle({ needs_onroad_cycle: true }), ctx({}));
    expect(s.disabled).toBe(false);
    expect(s.badges.some((b) => b.text === "需重启行程")).toBe(true);
  });

  it("badges offroad_only as a hint, never as a disable", () => {
    // 设备才是权威。网页禁用会在状态误判时永久锁死开关。
    const s = itemState(toggle({ enablement: [{ type: "offroad_only" }] }), ctx({}));
    expect(s.disabled).toBe(false);
    expect(s.badges.some((b) => b.text === "需停车")).toBe(true);
  });
});

describe("title suffix follows another param", () => {
  const item = toggle({
    title: "横向加速度因子",
    title_param_suffix: {
      param: "TorqueParamsOverrideEnabled",
      values: { true: "(Real-Time & Offline)", false: "(Offline Only)" },
    },
  });

  it("maps API booleans 1/0 onto the schema's true/false keys", () => {
    // /api/params 返回 "1"/"0"，而 schema 的 key 写的是 "true"/"false"
    expect(resolveTitle(item, { TorqueParamsOverrideEnabled: "1" })).toContain("Real-Time");
    expect(resolveTitle(item, { TorqueParamsOverrideEnabled: "0" })).toContain("Offline Only");
  });

  it("falls back to the bare title when the param is absent", () => {
    expect(resolveTitle(item, {})).toBe("横向加速度因子");
    expect(resolveTitle(toggle(), {})).toBe("启用 MADS");
  });
});

describe("sub panels", () => {
  it("is openable unconditionally when trigger_condition is null", () => {
    // 真实用例：cruise/speed_limit_settings 的 trigger_condition 是 null
    expect(subPanelOpenable(null, ctx({})).ok).toBe(true);
    expect(subPanelOpenable(undefined, ctx({})).ok).toBe(true);
  });

  it("gates on its trigger param otherwise", () => {
    const cond = { type: "param" as const, key: "Mads", equals: true };
    expect(subPanelOpenable(cond, ctx({ Mads: "1" })).ok).toBe(true);
    const shut = subPanelOpenable(cond, ctx({ Mads: "0" }));
    expect(shut.ok).toBe(false);
    expect(shut.reason).not.toBe("");
  });
});

describe("per-option availability", () => {
  it("disables individual buttons, as MadsSteeringMode does on rivian", () => {
    const rules = [
      { type: "not" as const, condition: { type: "capability" as const, field: "brand", equals: "rivian" } },
    ];
    expect(optionDisabled(rules, ctx({}, { brand: "toyota" })).disabled).toBe(false);
    const d = optionDisabled(rules, ctx({}, { brand: "rivian" }));
    expect(d.disabled).toBe(true);
    expect(d.reason).not.toBe("");
  });

  it("treats an option with no rules as available", () => {
    expect(optionDisabled(undefined, ctx({})).disabled).toBe(false);
  });
});
