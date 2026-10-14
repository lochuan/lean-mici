/** TechStream（丰田诊断）页的纯逻辑：hex 差异高亮、会话状态与倒计时。
 *  接口与类型见 schema.ts（后端 toyota_diag_api.py）。 */
import type { Tone } from "./diagnostics";
import type { ToyotaDtc, ToyotaDtcScan, ToyotaEcu, ToyotaLiveItem, ToyotaLiveValue, ToyotaScanStatus, ToyotaSession, ToyotaSetting } from "./schema";

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

// ---- 会话 ----

export interface SessionView {
  label: string;
  tone: Tone;
  detail: string;
  canEnter: boolean;
  canExit: boolean;
  canUseDiagnostics: boolean;
}

/** 会话状态 → 徽章 / 按钮可用性。session 为 null 表示还没拿到数据：什么都不让点。
 *  后端没有 error 状态：进入失败后回到 idle 并带 error，允许重试，也允许 /exit 清理。 */
export function sessionView(session: ToyotaSession | null): SessionView {
  switch (session?.state) {
    case "entering":
      return { label: "进入中", tone: "warn", detail: "", canEnter: false, canExit: false, canUseDiagnostics: false };
    case "active":
      return { label: "诊断中", tone: "accent", detail: "", canEnter: false, canExit: true, canUseDiagnostics: true };
    case "idle":
      return session.error
        ? { label: "未进入", tone: "danger", detail: session.error, canEnter: true, canExit: true, canUseDiagnostics: false }
        : { label: "未进入", tone: "muted", detail: "", canEnter: true, canExit: false, canUseDiagnostics: false };
    default:
      return { label: "未进入", tone: "muted", detail: "", canEnter: false, canExit: false, canUseDiagnostics: false };
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

/** 数值显示：固定小数位，整数位带千分位（里程）。 */
export function formatNumber(n: number, digits: number): string {
  return n.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// ---- 实时数据显示 ----

export const BATTERY_12V_KEY = "car_tool_battery_voltage_engine";
export const MILEAGE_KEY = "car_tool_mileage";
export const MILEAGE_UNIT_KEY = "car_tool_mileage_unit";

/** 首页车辆卡要读的三项：12V 电压、总里程、里程单位 */
export const HOME_LIVE_KEYS = [BATTERY_12V_KEY, MILEAGE_KEY, MILEAGE_UNIT_KEY] as const;

/** 一项实时数据的显示文字：数值格式由后端按目录的 precision 给好（text），这里只拼单位。 */
export function liveValueText(v: ToyotaLiveValue | undefined, unit = ""): string {
  if (!v || v.error || v.value === null) return "—";
  return unit ? `${v.text} ${unit}` : v.text;
}

/** 里程单位并进里程卡：单位是另一个实时数据项（1 = 公里、2 = 英里），读不到时按公里 */
function mileageParts(mileage: ToyotaLiveValue | undefined, unit: ToyotaLiveValue | undefined): { value: string; unit: string } | null {
  if (!mileage || mileage.error || mileage.value === null) return null;
  return { value: formatNumber(mileage.value, 0), unit: unit?.text || "公里" };
}

export function mileageText(mileage: ToyotaLiveValue | undefined, unit: ToyotaLiveValue | undefined): string {
  const parts = mileageParts(mileage, unit);
  return parts ? `${parts.value} ${parts.unit}` : "—";
}

// ---- 定制项 ----

export function ecuProtocolText(protocol: ToyotaEcu["protocol"]): string {
  return protocol === "toyota_can" ? "Toyota CAN（旧式）" : "UDS";
}

/** 撤销 / 恢复备份成功后的 toast：回读校验没通过要提醒，不报「已恢复」。 */
export function restoreToast(settingName: string, restoredLabel: string, verified: boolean): { message: string; tone: "ok" | "warn" } {
  return verified
    ? { message: `已恢复「${settingName}」→ ${restoredLabel}`, tone: "ok" }
    : { message: "已恢复，但回读校验未通过", tone: "warn" };
}

/** 一个设置当前值的显示文案：选项型取选项文字，数值型直接显示数字。 */
export function settingValueLabel(setting: ToyotaSetting, value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (setting.numeric) return String(value);
  return setting.options.find((o) => o.value === value)?.label ?? `其他（原始值 ${value}）`;
}

/** 定制扫描只需要问有定制项的 ECU（其余 ECU 问了也是白等一次超时），保持目录顺序。 */
export function ecusWithSettings(ecus: ToyotaEcu[], settings: ToyotaSetting[]): ToyotaEcu[] {
  const used = new Set(settings.map((s) => s.ecu));
  return ecus.filter((e) => used.has(e.id));
}

// ---- 扫描进度 ----

/** 首页「在线 ECU」：应答数 / 已探测数（定制扫描和故障码扫描探测的 ECU 集合不同，按 ECU 记应答才口径一致）。 */
export function onlineEcuText(ecuAnswered: Record<string, boolean>): string {
  const probed = Object.values(ecuAnswered);
  return probed.length ? `${probed.filter(Boolean).length} / ${probed.length}` : "—";
}

export interface ScanTally {
  done: number;
  total: number;
  percent: number;
}

/** 逐个 ECU 扫描的进度：已扫描几个、共几个，percent 给进度条用。 */
export function scanTally(statuses: ToyotaScanStatus[], total: number): ScanTally {
  return { done: statuses.length, total, percent: total ? (statuses.length / total) * 100 : 0 };
}

/** 写入后整段数据的预期样子（技术详情里和当前原始值做 hex 对比）：
 *  把 value 左移到 mask 最低置位位置，只替换 byteIndex 字节里被 mask 覆盖的位。 */
export function previewRaw(raw: string, byteIndex: number, mask: string, value: number): string {
  const bytes = raw.replace(/\s+/g, "").toUpperCase().match(/.{1,2}/g) ?? [];
  if (byteIndex >= bytes.length) return bytes.join("");
  const m = parseInt(mask, 16);
  const shift = Math.log2(m & -m);
  const next = (parseInt(bytes[byteIndex], 16) & ~m) | ((value << shift) & m);
  bytes[byteIndex] = next.toString(16).toUpperCase().padStart(2, "0");
  return bytes.join("");
}

/** 从整段 raw 取一个设置项的值：与后端 toyota/coding.py 的 extract_value 同语义——
 *  byteIndex 字节里被 mask 覆盖的位移到最低位。数据没有这个字节返回 null。 */
export function settingValueFromRaw(raw: string, byteIndex: number, mask: string): number | null {
  const bytes = toBytes(raw);
  if (byteIndex >= bytes.length) return null;
  const maskBits = parseInt(mask, 16);
  const shift = Math.log2(maskBits & -maskBits);
  return (parseInt(bytes[byteIndex], 16) & maskBits) >> shift;
}

/** 同一 ECU、同一 DID（UDS）/ localId（Toyota CAN）的设置项共用一段 raw。
 *  changed 写入或恢复后整段数据变成 newRaw：返回所有已扫描的同段设置项的新 {value, raw}。
 *  新 raw 里取不到值的项不返回（保持原样）。 */
export function settingValuesAfterRawChange(
  settings: ToyotaSetting[],
  changed: ToyotaSetting,
  newRaw: string,
  scannedValues: Record<string, { value: number; raw: string }>,
): Record<string, { value: number; raw: string }> {
  const sharesData = (s: ToyotaSetting) => s.ecu === changed.ecu && (s.did ?? s.localId) === (changed.did ?? changed.localId);
  const updated: Record<string, { value: number; raw: string }> = {};
  for (const s of settings.filter(sharesData)) {
    const value = settingValueFromRaw(newRaw, s.byteIndex, s.mask);
    if (s.key in scannedValues && value !== null) updated[s.key] = { value, raw: newRaw };
  }
  return updated;
}

// ---- 故障码 ----

export interface DtcTag {
  label: string;
  tone: Tone;
}

/** UDS 状态字节 → 标签：bit0 当前故障，bit2 待定，bit3 已确认，bit7 请求点亮故障灯；
 *  已确认且非当前 = 历史。Toyota CAN 的码没有状态（null），视为已确认。 */
export function dtcStatusTags(status: number | null): DtcTag[] {
  if (status === null) return [{ label: "已确认", tone: "muted" }];
  const current = (status & 0x01) !== 0;
  const pending = (status & 0x04) !== 0;
  const confirmed = (status & 0x08) !== 0;
  const mil = (status & 0x80) !== 0;
  const tags: DtcTag[] = [];
  if (current) tags.push({ label: "当前故障", tone: "danger" });
  if (confirmed && current) tags.push({ label: "已确认", tone: "danger" });
  if (pending) tags.push({ label: "待定", tone: "warn" });
  if (confirmed && !current) tags.push({ label: "历史", tone: "muted" });
  if (mil) tags.push({ label: "请求点亮故障灯", tone: "warn" });
  return tags;
}

/** 同一 ECU 里「FTB 87（报文丢失）且非当前」的码折成一组：多半是拆装线束或断电留下的历史记录。 */
export function groupDtcs(dtcs: ToyotaDtc[]): { shown: ToyotaDtc[]; history: ToyotaDtc[] } {
  const isHistoryLoss = (d: ToyotaDtc) => d.code.endsWith("-87") && d.status !== null && (d.status & 0x01) === 0;
  return { shown: dtcs.filter((d) => !isHistoryLoss(d)), history: dtcs.filter(isHistoryLoss) };
}

export function dtcDescription(d: ToyotaDtc): string {
  return d.text || "暂无中文说明";
}

export type DtcScanState = ToyotaScanStatus | "faulty" | "clear" | "rejected" | "unscanned";

/** 一个 ECU 故障码扫描结果的显示状态。后端对「ECU 拒绝读码」仍回 status "ok"，只带 readError、dtcs 为空，
 *  不能显示成「无故障」。undefined = 这个 ECU 没扫到。 */
export function dtcScanState(scan: ToyotaDtcScan | undefined): DtcScanState {
  if (!scan) return "unscanned";
  if (scan.status !== "ok") return scan.status;
  if (scan.readError) return "rejected";
  return scan.dtcs.length ? "faulty" : "clear";
}

export function dtcSummary(scans: ToyotaDtcScan[]): { ecuCount: number; faultyEcuCount: number; codeCount: number; rejectedEcuCount: number } {
  return {
    ecuCount: scans.length,
    faultyEcuCount: scans.filter((s) => s.dtcs.length > 0).length,
    codeCount: scans.reduce((n, s) => n + s.dtcs.length, 0),
    rejectedEcuCount: scans.filter((s) => dtcScanState(s) === "rejected").length,
  };
}

/** 故障数多的 ECU 排前面；同数保持目录顺序（sort 稳定）。没扫到的按 0 条算。 */
export function sortEcusByDtcCount<T extends { id: string }>(ecus: T[], scans: ToyotaDtcScan[]): T[] {
  const count = (id: string) => scans.find((s) => s.ecu === id)?.dtcs.length ?? 0;
  return [...ecus].sort((a, b) => count(b.id) - count(a.id));
}

/** 首页「故障码」入口的说明；scans 为 null 表示还没扫描过 */
export function dtcSummaryText(scans: ToyotaDtcScan[] | null): string {
  if (!scans) return "尚未扫描";
  const { faultyEcuCount, codeCount, rejectedEcuCount } = dtcSummary(scans);
  const rejectedNote = rejectedEcuCount ? `（${rejectedEcuCount} 个 ECU 拒绝读码）` : "";
  return (codeCount ? `上次扫描：${faultyEcuCount} 个 ECU 共 ${codeCount} 条故障码` : "上次扫描：未发现故障码") + rejectedNote;
}

export interface LiveCard {
  key: string;
  name: string;
  value: string;
  unit: string;
  needsEngineRunning: boolean;
}

/** 一个主题页的数据卡：按 items 顺序，里程单位项并进里程卡不单独成卡，读不到的项显示 —。 */
export function liveCards(items: ToyotaLiveItem[], values: ToyotaLiveValue[]): LiveCard[] {
  const byKey = new Map(values.map((v) => [v.key, v]));
  return items
    .filter((it) => it.key !== MILEAGE_UNIT_KEY)
    .map((it) => {
      const v = byKey.get(it.key);
      const base = { key: it.key, name: it.name, needsEngineRunning: it.needsEngineRunning };
      if (!v || v.error || v.value === null) return { ...base, value: "—", unit: "" };
      if (it.key === MILEAGE_KEY) return { ...base, ...mileageParts(v, byKey.get(MILEAGE_UNIT_KEY))! };
      return { ...base, value: v.text, unit: it.unit };
    });
}
