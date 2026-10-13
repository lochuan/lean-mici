/** 丰田诊断页的纯逻辑：hex 差异高亮、定制项分组、PID 勾选、会话状态与倒计时。
 *  接口契约见 .scratch/toyota-diag/spec.md §3。 */
import type { Tone } from "./diagnostics";
import type { ToyotaCustomizationItem, ToyotaPidGroup, ToyotaSession } from "./schema";

/** 单次 pids/read 的 id 上限（后端同样限制） */
export const PID_SELECTION_LIMIT = 40;

// ---- hex 差异 ----

export interface HexByte {
  byte: string;
  changed: boolean;
}

function toBytes(hex: string): string[] {
  const clean = hex.replace(/\s+/g, "").toUpperCase();
  return clean.match(/.{1,2}/g) ?? [];
}

/** 逐字节对比改前 / 改后；长度不同时，缺的一侧不补位，多出的字节算差异。 */
export function hexDiff(before: string, after: string): { before: HexByte[]; after: HexByte[] } {
  const a = toBytes(before);
  const b = toBytes(after);
  const mark = (own: string[], other: string[]): HexByte[] =>
    own.map((byte, i) => ({ byte, changed: byte !== other[i] }));
  return { before: mark(a, b), after: mark(b, a) };
}

// ---- 定制项 ----

export interface CustomizationGroup {
  group: string;
  items: ToyotaCustomizationItem[];
}

const zh = (a: string, b: string) => a.localeCompare(b, "zh");

/** 按 group 分组；组按中文序，组内按名称再按 uid。 */
export function groupCustomizations(items: ToyotaCustomizationItem[]): CustomizationGroup[] {
  const byGroup = new Map<string, ToyotaCustomizationItem[]>();
  for (const it of items) {
    const list = byGroup.get(it.group);
    if (list) list.push(it);
    else byGroup.set(it.group, [it]);
  }
  return [...byGroup.entries()]
    .sort(([a], [b]) => zh(a, b))
    .map(([group, list]) => ({ group, items: [...list].sort((x, y) => zh(x.name, y.name) || x.uid - y.uid) }));
}

// ---- 实时数据 PID ----

/** 勾选 / 取消勾选；已满额时拒绝新增并置 overLimit 供界面提示。 */
export function togglePid(selected: number[], id: number): { selected: number[]; overLimit: boolean } {
  if (selected.includes(id)) return { selected: selected.filter((s) => s !== id), overLimit: false };
  if (selected.length >= PID_SELECTION_LIMIT) return { selected, overLimit: true };
  return { selected: [...selected, id], overLimit: false };
}

/** 按名称（不区分大小写）过滤，没有命中的 ECU 组整组丢弃。 */
export function filterPidGroups(groups: ToyotaPidGroup[], query: string): ToyotaPidGroup[] {
  const q = query.trim().toLowerCase();
  if (!q) return groups;
  return groups
    .map((g) => ({ ecu: g.ecu, pids: g.pids.filter((p) => p.name.toLowerCase().includes(q)) }))
    .filter((g) => g.pids.length > 0);
}

// ---- 会话 ----

export interface SessionView {
  label: string;
  tone: Tone;
  detail: string;
  canEnter: boolean;
  canExit: boolean;
  tabsEnabled: boolean;
}

/** 会话状态 → 徽章 / 按钮可用性。session 为 null 表示还没拿到数据：什么都不让点。
 *  后端没有 error 状态：进入失败后回到 idle 并带 error，允许重试，也允许 /exit 清理。 */
export function sessionView(session: ToyotaSession | null): SessionView {
  switch (session?.state) {
    case "entering":
      return { label: "进入中", tone: "warn", detail: "", canEnter: false, canExit: false, tabsEnabled: false };
    case "active":
      return { label: "诊断中", tone: "accent", detail: "", canEnter: false, canExit: true, tabsEnabled: true };
    case "idle":
      return session.error
        ? { label: "未进入", tone: "danger", detail: session.error, canEnter: true, canExit: true, tabsEnabled: false }
        : { label: "未进入", tone: "muted", detail: "", canEnter: true, canExit: false, tabsEnabled: false };
    default:
      return { label: "未进入", tone: "muted", detail: "", canEnter: false, canExit: false, tabsEnabled: false };
  }
}

/** 距离空闲自动退出还剩多少秒：设备给的 idleRemainingS 减去拿到它之后本地过去的秒数
 *  （不用浏览器时钟比对设备时间，避免两边时钟偏差）。 */
export function idleRemainingS(session: ToyotaSession | null, elapsedS: number): number | null {
  if (session?.state !== "active" || session.idleRemainingS === undefined) return null;
  return Math.max(0, session.idleRemainingS - elapsedS);
}

export function countdownText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** 任意抛出物 → 提示文案。ApiError 的 message 已是服务端 {"error"} 文案。 */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
