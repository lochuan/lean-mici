/** 横向扭矩自整定卡片的纯逻辑：整体状态、每个速度档的显示行。
 *
 * 生效口径与车端一致：controlsd 只在 useParams 时把学到的值用到横向控制上；
 * 分段插值还要求消息里带速度档（SpeedDependentTorqueToggle 开启且 torqued 已按开关重启）。
 * 某档 valid=false 时控制器用的是全局值，所以“学习中”的档显示的数字并未生效。
 */
import type { TorqueBin, TorqueStatus } from "./schema";

export type Tone = "accent" | "warn" | "info" | "muted";

export interface Headline {
  text: string;
  tone: Tone;
}

export function torqueHeadline(t: TorqueStatus | null | undefined): Headline {
  if (!t) return { text: "—", tone: "muted" };
  const ctl = t.offline.lateralControl;
  if (ctl && ctl !== "torque") return { text: `非扭矩控制（${ctl}）`, tone: "muted" };
  const l = t.learned;
  if (!l) return { text: "暂无学习数据", tone: "muted" };
  if (!l.useParams) return { text: "未启用（使用出厂值）", tone: "muted" };
  if (l.valid) return { text: "自整定生效中", tone: "accent" };
  return { text: `学习中 ${l.calPerc}%`, tone: "warn" };
}

export function sourceText(source: TorqueStatus["source"]): string {
  switch (source) {
    case "live": return "实时";
    case "cache": return "上次行驶缓存";
    default: return "无数据";
  }
}

/** 分段开关状态提示：开关开了但数据里没有速度档 → 需开一次车（torqued 启动时才读开关） */
export function speedDepHint(t: TorqueStatus | null | undefined): string | null {
  if (!t) return null;
  const on = t.toggles.SpeedDependentTorqueToggle;
  const hasBins = (t.learned?.bins.length ?? 0) > 0;
  if (on && !hasBins) return "按车速分段已开启，下次行驶后开始分档学习";
  if (!on && hasBins) return "按车速分段已关闭，下面是关闭前学到的分档数据，当前不生效";
  return null;
}

export interface BinRow {
  key: number;
  /** km/h 区间，如 "18–29" */
  range: string;
  latAccelFactor: string;
  friction: string;
  /** 相对全局 LAF 的偏差，如 "+8%"；全局无效时为 "" */
  delta: string;
  /** 进度条宽度 0–100；未知为 null */
  progress: number | null;
  state: string;
  tone: Tone;
}

const KPH = 3.6;

export function binRows(bins: TorqueBin[], globalLaf: number): BinRow[] {
  return bins.map((b, i) => {
    const pct = globalLaf > 0 ? Math.round((b.latAccelFactor / globalLaf - 1) * 100) : null;
    const progress = b.calPerc === null ? null : Math.max(0, Math.min(100, b.calPerc));
    return {
      key: i,
      range: `${Math.round(b.lo * KPH)}–${Math.round(b.hi * KPH)}`,
      latAccelFactor: b.latAccelFactor.toFixed(3),
      friction: b.friction.toFixed(3),
      delta: pct === null ? "" : `${pct > 0 ? "+" : ""}${pct}%`,
      progress: b.valid ? 100 : progress,
      state: b.valid ? "已生效" : progress === null ? "学习中" : `学习中 ${progress}%`,
      tone: b.valid ? "accent" : "warn",
    };
  });
}
