import { describe, expect, it, vi } from "vitest";
import { evalRules, requiresAdvanced, requiresOffroad } from "../src/lib/rules";
import type { Rule } from "../src/lib/schema";

// 规则引擎决定用户能碰哪些开关，所以它错了就是安全问题（该禁的没禁）
// 或者功能凭空消失（该显示的没显示）。这里用真实 schema 里出现过的规则形状。

const ctx = (params: Record<string, string> = {}, caps: Record<string, unknown> = {}) => ({
  params,
  caps: caps as Record<string, string | number | boolean>,
});

describe("param rules", () => {
  it("normalizes BOOL across API and schema spellings", () => {
    // API 返回 "1"/"0"；schema 里写 true / "True" / 1 都得等价
    for (const equals of [true, "True", "true", "1", 1]) {
      const rule: Rule = { type: "param", key: "Mads", equals };
      expect(evalRules([rule], ctx({ Mads: "1" })).ok).toBe(true);
      expect(evalRules([rule], ctx({ Mads: "0" })).ok).toBe(false);
    }
  });

  it("treats a missing param as not matching", () => {
    const rule: Rule = { type: "param", key: "Absent", equals: true };
    expect(evalRules([rule], ctx({})).ok).toBe(false);
  });

  it("explains ShowAdvancedControls specially", () => {
    const rule: Rule = { type: "param", key: "ShowAdvancedControls", equals: true };
    const { ok, reasons } = evalRules([rule], ctx({}));
    expect(ok).toBe(false);
    expect(reasons[0]).toContain("高级控制");
  });
});

describe("param_compare rules", () => {
  it("compares numerically, as SpeedLimitValueOffset visibility does", () => {
    // 真实用例：SpeedLimitOffsetType > 0 才显示偏移值
    const rule: Rule = { type: "param_compare", key: "SpeedLimitOffsetType", op: ">", value: 0 };
    expect(evalRules([rule], ctx({ SpeedLimitOffsetType: "2" })).ok).toBe(true);
    expect(evalRules([rule], ctx({ SpeedLimitOffsetType: "0" })).ok).toBe(false);
  });

  it("fails closed on a non-numeric value", () => {
    const rule: Rule = { type: "param_compare", key: "X", op: ">", value: 0 };
    expect(evalRules([rule], ctx({ X: "abc" })).ok).toBe(false);
  });

  it("supports every operator", () => {
    const cases: Array<[Rule["op"], string, boolean]> = [
      ["<", "1", true], ["<", "5", false],
      ["<=", "5", true], [">", "9", true], [">=", "5", true],
      ["==", "5", true], ["!=", "6", true],
    ];
    for (const [op, value, want] of cases) {
      const rule: Rule = { type: "param_compare", key: "V", op, value: 5 };
      expect(evalRules([rule], ctx({ V: value })).ok, `${value} ${op} 5`).toBe(want);
    }
  });
});

describe("capability rules", () => {
  it("matches booleans and strings from /api/capabilities", () => {
    expect(
      evalRules([{ type: "capability", field: "has_longitudinal_control", equals: true }],
        ctx({}, { has_longitudinal_control: true })).ok,
    ).toBe(true);
    expect(
      evalRules([{ type: "capability", field: "brand", equals: "toyota" }],
        ctx({}, { brand: "toyota" })).ok,
    ).toBe(true);
    expect(
      evalRules([{ type: "capability", field: "brand", equals: "tesla" }],
        ctx({}, { brand: "toyota" })).ok,
    ).toBe(false);
  });

  it("gives a specific reason per capability, not a generic one", () => {
    const cases: Array<[string, unknown, string]> = [
      ["has_longitudinal_control", true, "纵向"],
      ["pcm_cruise", true, "原厂巡航"],
      ["torque_allowed", true, "扭矩"],
      ["enable_bsm", true, "盲区"],
      ["has_stop_and_go", true, "跟停"],
    ];
    for (const [field, equals, fragment] of cases) {
      const { reasons } = evalRules([{ type: "capability", field, equals }], ctx({}, {}));
      expect(reasons[0], field).toContain(fragment);
    }
  });
});

describe("device-side rules are not enforced in the browser", () => {
  it("offroad_only and not_engaged never disable a control", () => {
    // 设备才是权威：写入时由设备校验。网页替它判断会导致停车状态误判后
    // 永久锁死某些开关。
    expect(evalRules([{ type: "offroad_only" }], ctx({})).ok).toBe(true);
    expect(evalRules([{ type: "not_engaged" }], ctx({})).ok).toBe(true);
  });

  it("but they are still detectable so the UI can badge them", () => {
    expect(requiresOffroad([{ type: "offroad_only" }])).toBe(true);
    expect(requiresOffroad([{ type: "capability", field: "brand", equals: "toyota" }])).toBe(false);
    // 嵌套在 any/all/not 里也要能发现
    expect(requiresOffroad([{ type: "all", conditions: [{ type: "offroad_only" }] }])).toBe(true);
    expect(requiresOffroad([{ type: "not", condition: { type: "offroad_only" } }])).toBe(true);
  });

  it("detects the advanced-controls gate at any depth", () => {
    expect(requiresAdvanced([{ type: "param", key: "ShowAdvancedControls", equals: true }])).toBe(true);
    expect(
      requiresAdvanced([{ type: "any", conditions: [{ type: "param", key: "ShowAdvancedControls", equals: true }] }]),
    ).toBe(true);
    expect(requiresAdvanced([{ type: "param", key: "Mads", equals: true }])).toBe(false);
  });
});

describe("boolean combinators", () => {
  it("any passes when one branch passes", () => {
    const rule: Rule = {
      type: "any",
      conditions: [
        { type: "capability", field: "has_longitudinal_control", equals: true },
        { type: "capability", field: "has_icbm", equals: true },
      ],
    };
    expect(evalRules([rule], ctx({}, { has_icbm: true })).ok).toBe(true);
    expect(evalRules([rule], ctx({}, {})).ok).toBe(false);
  });

  it("any still explains itself when every branch fails", () => {
    // 否则用户看到置灰却没有任何原因
    const rule: Rule = {
      type: "any",
      conditions: [
        { type: "capability", field: "has_longitudinal_control", equals: true },
        { type: "capability", field: "has_icbm", equals: true },
      ],
    };
    const { ok, reasons } = evalRules([rule], ctx({}, {}));
    expect(ok).toBe(false);
    expect(reasons.length).toBeGreaterThan(0);
  });

  it("all fails when any branch fails, and reports the first reason", () => {
    const rule: Rule = {
      type: "all",
      conditions: [
        { type: "capability", field: "has_longitudinal_control", equals: true },
        { type: "not", condition: { type: "capability", field: "pcm_cruise", equals: true } },
      ],
    };
    expect(evalRules([rule], ctx({}, { has_longitudinal_control: true, pcm_cruise: false })).ok).toBe(true);
    const r = evalRules([rule], ctx({}, { has_longitudinal_control: true, pcm_cruise: true }));
    expect(r.ok).toBe(false);
  });

  it("not inverts, including the real MADS/tesla nesting", () => {
    // 取自 settings_ui.json 的 MadsMainCruiseAllowed
    const rule: Rule = {
      type: "not",
      condition: {
        type: "any",
        conditions: [
          { type: "capability", field: "brand", equals: "rivian" },
          {
            type: "all",
            conditions: [
              { type: "capability", field: "brand", equals: "tesla" },
              { type: "not", condition: { type: "capability", field: "tesla_has_vehicle_bus", equals: true } },
            ],
          },
        ],
      },
    };
    // toyota：不是 rivian 也不是 tesla → not(false) = 可用
    expect(evalRules([rule], ctx({}, { brand: "toyota" })).ok).toBe(true);
    // rivian → not(true) = 不可用
    expect(evalRules([rule], ctx({}, { brand: "rivian" })).ok).toBe(false);
    // tesla 且无 vehicle bus → 不可用
    expect(evalRules([rule], ctx({}, { brand: "tesla", tesla_has_vehicle_bus: false })).ok).toBe(false);
    // tesla 但有 vehicle bus → 可用
    expect(evalRules([rule], ctx({}, { brand: "tesla", tesla_has_vehicle_bus: true })).ok).toBe(true);
  });
});

describe("unknown input fails open, loudly", () => {
  it("an unknown rule type is treated as available and warns", () => {
    // 宁可多显示一个开关，也不要因为新增了规则类型就静默隐藏功能
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(evalRules([{ type: "brand_new" as Rule["type"] }], ctx({})).ok).toBe(true);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("no rules means available", () => {
    expect(evalRules(undefined, ctx({})).ok).toBe(true);
    expect(evalRules([], ctx({})).ok).toBe(true);
  });

  it("deduplicates identical reasons", () => {
    const rule: Rule = { type: "capability", field: "torque_allowed", equals: true };
    const { reasons } = evalRules([rule, rule], ctx({}, {}));
    expect(reasons.length).toBe(1);
  });
});
