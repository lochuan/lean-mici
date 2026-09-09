// enablement/visibility 规则求值 + 中文原因收集（设计 §5.3）。
// offroad_only/not_engaged 本地不限制（写入侧由设备自身校验）。
import { store } from "./store.js";

export function evalRules(rules) {
  const reasons = [];
  const ok = (rules || []).every(r => {
    const [res, why] = evalRule(r);
    if (!res && why && !reasons.includes(why)) reasons.push(why);
    return res;
  });
  return { ok, reasons };
}

function evalRule(r) {
  if (!r || typeof r !== "object") { console.warn("未知规则，按可用处理:", r); return [true, null]; }
  switch (r.type) {
    case "offroad_only":
    case "not_engaged":
      return [true, null];
    case "capability": {
      const ok = store.caps[r.field] === r.equals;
      return [ok, ok ? null : capReason(r)];
    }
    case "param": {
      const norm = (v) => (v === true ? "1" : v === false ? "0" : String(v));
      const actual = norm(store.paramsAll[r.key]);
      const expected = { True: "1", true: "1", False: "0", false: "0" }[norm(r.equals)] ?? norm(r.equals);
      const ok = actual === expected;
      return [ok, ok ? null : (r.key === "ShowAdvancedControls"
        ? "需在“通用”面板开启高级控制"
        : `前置条件未满足：${r.key}`)];
    }
    case "param_compare": {
      const v = Number(store.paramsAll[r.key]);
      const ok = !Number.isNaN(v) && compare(v, r.op, r.value);
      return [ok, ok ? null : `前置条件未满足：${r.key}`];
    }
    case "any": return [(r.conditions || []).some(c => evalRule(c)[0]), null];
    case "all": {
      let why = null;
      const ok = (r.conditions || []).every(c => {
        const [o, w] = evalRule(c);
        if (!o && w && !why) why = w;
        return o;
      });
      return [ok, ok ? null : why];
    }
    case "not": return r.condition ? [!evalRule(r.condition)[0], null] : [true, null];
    default:
      console.warn("未知规则，按可用处理:", r);
      return [true, null];
  }
}

function compare(v, op, ref) {
  switch (op) {
    case "<": return v < ref;
    case "<=": return v <= ref;
    case ">": return v > ref;
    case ">=": return v >= ref;
    case "==": return v === ref;
    case "!=": return v !== ref;
    default: console.warn("未知比较符，按可用处理:", op); return true;
  }
}

function capReason(r) {
  if (r.field === "steer_control_type" && r.equals === "angle") return "仅角度转向车型可用";
  if (r.field === "torque_allowed") return "需要扭矩转向车型";
  return "当前车型/配置不支持";
}
