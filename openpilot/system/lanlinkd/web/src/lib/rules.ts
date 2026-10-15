/** enablement / visibility 规则求值 + 中文原因。
 *
 * 从旧版 static/js/rules.js 移植，语义保持一致：
 *   - offroad_only / not_engaged 在网页侧**不限制**（写入由设备自身校验）。
 *     UI 只提示"需停车"，不替设备做决定——设备才是唯一权威。
 *   - 未知规则类型按"可用"处理并告警，宁可多显示也不要静默隐藏功能。
 *
 * 返回 reasons 是关键：sunnylink 的做法是**置灰 + 说明原因**，不是隐藏
 * （实测 UNAVAILABLE 徽章），否则用户以为功能消失了。见 FRONTEND_SPEC.md §3。
 */
import type { Capabilities, ParamValues, Rule } from "./schema";

export interface RuleContext {
  params: ParamValues;
  caps: Capabilities;
}

export interface RuleResult {
  ok: boolean;
  reasons: string[];
}

/** param 值归一化：BOOL 在 API 里是 "1"/"0"，schema 里可能写 true/"True" */
function norm(v: unknown): string {
  if (v === true) return "1";
  if (v === false) return "0";
  if (v === null || v === undefined) return "";
  return String(v);
}

const BOOL_ALIASES: Record<string, string> = {
  True: "1",
  true: "1",
  "1": "1",
  False: "0",
  false: "0",
  "0": "0",
};

function expected(v: unknown): string {
  const s = norm(v);
  return BOOL_ALIASES[s] ?? s;
}

function compare(v: number, op: Rule["op"], ref: number): boolean {
  switch (op) {
    case "<":
      return v < ref;
    case "<=":
      return v <= ref;
    case ">":
      return v > ref;
    case ">=":
      return v >= ref;
    case "==":
      return v === ref;
    case "!=":
      return v !== ref;
    default:
      console.warn("未知比较符，按可用处理:", op);
      return true;
  }
}

function capabilityReason(rule: Rule): string {
  if (rule.field === "steer_control_type" && rule.equals === "angle")
    return "仅角度转向车型可用";
  if (rule.field === "torque_allowed") return "需要扭矩转向车型";
  if (rule.field === "has_longitudinal_control") return "需要 openpilot 纵向控制";
  if (rule.field === "pcm_cruise") return "车辆使用原厂巡航（PCM）";
  if (rule.field === "has_icbm" || rule.field === "icbm_available")
    return "需要智能巡航按键管理 (ICBM)";
  if (rule.field === "enable_bsm") return "需要车辆支持盲区监测 (BSM)";
  if (rule.field === "has_stop_and_go") return "需要车辆支持自动跟停起步";
  if (rule.field === "device_type") return "当前设备型号不支持";
  return "当前车型/配置不支持";
}

function paramReason(key: string | undefined): string {
  if (key === "ShowAdvancedControls") return "需在「开发者」面板开启高级控制";
  return `前置条件未满足：${key ?? "?"}`;
}

function evalRule(rule: Rule | undefined, ctx: RuleContext): [boolean, string | null] {
  if (!rule || typeof rule !== "object") {
    console.warn("未知规则，按可用处理:", rule);
    return [true, null];
  }

  switch (rule.type) {
    // 设备侧条件：网页不代为判断，仅由设备在写入时拒绝
    case "offroad_only":
    case "not_engaged":
      return [true, null];

    case "capability": {
      const ok = norm(ctx.caps[rule.field ?? ""]) === norm(rule.equals);
      return [ok, ok ? null : capabilityReason(rule)];
    }

    case "param": {
      const ok = norm(ctx.params[rule.key ?? ""]) === expected(rule.equals);
      return [ok, ok ? null : paramReason(rule.key)];
    }

    case "param_compare": {
      const v = Number(ctx.params[rule.key ?? ""]);
      const ok = !Number.isNaN(v) && compare(v, rule.op, rule.value ?? 0);
      return [ok, ok ? null : paramReason(rule.key)];
    }

    case "any": {
      const conds = rule.conditions ?? [];
      const ok = conds.some((c) => evalRule(c, ctx)[0]);
      if (ok) return [true, null];
      // any 全不满足时，取第一条有原因的子条件作为解释
      for (const c of conds) {
        const [, why] = evalRule(c, ctx);
        if (why) return [false, why];
      }
      return [false, null];
    }

    case "all": {
      let why: string | null = null;
      const ok = (rule.conditions ?? []).every((c) => {
        const [o, w] = evalRule(c, ctx);
        if (!o && w && !why) why = w;
        return o;
      });
      return [ok, ok ? null : why];
    }

    case "not": {
      if (!rule.condition) return [true, null];
      const [inner] = evalRule(rule.condition, ctx);
      return [!inner, inner ? "当前配置下不适用" : null];
    }

    default:
      console.warn("未知规则，按可用处理:", rule);
      return [true, null];
  }
}

export function evalRules(rules: Rule[] | undefined, ctx: RuleContext): RuleResult {
  const reasons: string[] = [];
  const ok = (rules ?? []).every((r) => {
    const [res, why] = evalRule(r, ctx);
    if (!res && why && !reasons.includes(why)) reasons.push(why);
    return res;
  });
  return { ok, reasons };
}

/** 是否存在 offroad_only 约束（用于显示「需停车」徽章而不禁用控件） */
export function requiresOffroad(rules: Rule[] | undefined): boolean {
  const walk = (r: Rule | undefined): boolean => {
    if (!r) return false;
    if (r.type === "offroad_only") return true;
    if (r.condition) return walk(r.condition);
    return (r.conditions ?? []).some(walk);
  };
  return (rules ?? []).some(walk);
}

export function requiresAdvanced(rules: Rule[] | undefined): boolean {
  const walk = (r: Rule | undefined): boolean => {
    if (!r) return false;
    if (r.type === "param" && r.key === "ShowAdvancedControls") return true;
    if (r.condition) return walk(r.condition);
    return (r.conditions ?? []).some(walk);
  };
  return (rules ?? []).some(walk);
}
