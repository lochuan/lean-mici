/** 把规则求值 + schema 标记合成"这个控件现在长什么样"。
 *
 * 复刻 sunnylink 的处理方式（实测）：**不可用就置灰 + 徽章 + 说明原因，
 * 绝不隐藏**——只有 visibility 规则为假时才真正不渲染（那是"此车型无此概念"，
 * 不是"暂时不能改"）。
 */
import type { Capabilities, Item, ParamValues, Rule } from "./schema";
import { evalRules, requiresAdvanced, requiresOffroad, type RuleContext } from "./rules";

export type BadgeKind = "muted" | "warn" | "danger" | "info" | "accent";

export interface Badge {
  text: string;
  kind: BadgeKind;
  title?: string;
}

export interface ItemState {
  visible: boolean;
  disabled: boolean;
  badges: Badge[];
  /** 置灰原因，显示在描述位置 */
  reasons: string[];
}

export function itemState(item: Item, ctx: RuleContext): ItemState {
  const badges: Badge[] = [];
  const reasons: string[] = [];

  // visibility：不适用于本车型/配置 → 真的不渲染
  const vis = evalRules(item.visibility, ctx);
  if (!vis.ok) return { visible: false, disabled: true, badges, reasons: vis.reasons };

  let disabled = false;

  // 设备上没有这个 param：写入必然 404，禁用并说明
  if (item._missing) {
    disabled = true;
    badges.push({ text: "不可用", kind: "muted", title: "当前固件中不存在此设置项" });
    reasons.push("此版本固件未提供该设置");
  }

  // schema 声明的远程禁改项（AdbEnabled / SshEnabled）：后端也会返回 403
  if (item.blocked) {
    disabled = true;
    badges.push({ text: "仅设备端", kind: "danger", title: "出于安全考虑，只能在车机屏幕上修改" });
    reasons.push("出于安全考虑，此项只能在车机上修改");
  }

  const en = evalRules(item.enablement, ctx);
  if (!en.ok) {
    disabled = true;
    for (const r of en.reasons) if (!reasons.includes(r)) reasons.push(r);
    badges.push({ text: "不可用", kind: "muted" });
  }

  // 下面两个是"提示"而非禁用：offroad_only 由设备在写入时裁决，
  // 网页替它判断会因状态误判而永久锁死开关（见 rules.ts）
  if (requiresOffroad(item.enablement)) {
    badges.push({ text: "需停车", kind: "warn", title: "行驶中设备会拒绝此修改" });
  }
  if (item.needs_onroad_cycle) {
    badges.push({ text: "需重启行程", kind: "info", title: "下次上下电循环后生效" });
  }
  if (requiresAdvanced(item.enablement) && !en.ok) {
    badges.push({ text: "高级", kind: "accent" });
  }

  return { visible: true, disabled, badges, reasons };
}

/** 选项级可用性（multiple_button 的单个按钮可以单独禁用） */
export function optionDisabled(
  rules: Rule[] | undefined,
  ctx: RuleContext,
): { disabled: boolean; reason: string } {
  const r = evalRules(rules, ctx);
  return { disabled: !r.ok, reason: r.reasons[0] ?? "" };
}

/** 标题后缀：随另一个 param 的当前值变化 */
export function resolveTitle(item: Item, params: ParamValues): string {
  const suffix = item.title_param_suffix;
  if (!suffix) return item.title;
  const raw = params[suffix.param];
  const key = raw === "1" ? "true" : raw === "0" ? "false" : String(raw);
  const extra = suffix.values[key];
  return extra ? `${item.title} ${extra}` : item.title;
}

/** sub_panel 是否可进入（trigger_condition 为 null 表示无条件） */
export function subPanelOpenable(
  cond: Rule | null | undefined,
  ctx: RuleContext,
): { ok: boolean; reason: string } {
  if (!cond) return { ok: true, reason: "" };
  const r = evalRules([cond], ctx);
  return { ok: r.ok, reason: r.reasons[0] ?? "" };
}

/** 当前车型对应的品牌专属设置分组 */
export function brandOf(caps: Capabilities): string {
  return String(caps.brand ?? "");
}

export type { RuleContext };
