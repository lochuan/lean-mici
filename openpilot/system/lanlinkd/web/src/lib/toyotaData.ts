/** TechStream 各页面共享的数据：目录与各次扫描的结果。
 *
 * 放在模块里是因为页面栈会销毁离开的页面：从分类子页退回定制页不能重新扫描。
 * 退出诊断模式（或空闲超时）后 resetScanData() 清掉全部扫描结果，因为车上的值可能已被别处改动。
 */
import { reactive } from "vue";
import { api, ApiError } from "./api";
import type { BatteryAnalysis } from "./battery";
import type { ToyotaCatalog, ToyotaDtcScan, ToyotaEcu, ToyotaScanStatus, ToyotaSetting } from "./schema";
import { ecusWithSettings, errorText, settingValuesAfterRawChange } from "./toyota";

export const toyotaData = reactive({
  catalog: null as ToyotaCatalog | null,
  catalogError: "",
  /** ECU id → 最近一次探测有没有应答（定制扫描和故障码扫描都更新）；没探测过的 ECU 不在表里 */
  ecuAnswered: {} as Record<string, boolean>,
  customization: {
    state: "idle" as "idle" | "scanning" | "done",
    /** 已扫描完的 ECU 的状态，长度 = 已完成数 */
    statuses: [] as ToyotaScanStatus[],
    /** 无应答 / 应答异常的 ECU，扫描完给用户解释为什么少了项 */
    unreachable: [] as ToyotaEcu[],
    /** 设置项 key → 当前值与扫描时的原始数据（写入时作为 before 校验 ECU 数据没被别处改过） */
    values: {} as Record<string, { value: number; raw: string }>,
  },
  dtc: {
    state: "idle" as "idle" | "scanning" | "done",
    /** 按目录顺序，已扫描完的 ECU 的故障码 */
    scans: [] as ToyotaDtcScan[],
  },
  battery: {
    /** 最近一次读取的判定；还没读过或读不全为 null */
    analysis: null as BatteryAnalysis | null,
  },
});

let catalogLoading = false;

export async function loadCatalog(): Promise<void> {
  if (catalogLoading || toyotaData.catalog) return;
  catalogLoading = true;
  toyotaData.catalogError = "";
  try {
    toyotaData.catalog = await api.toyotaCatalog();
  } catch (e) {
    toyotaData.catalogError = errorText(e);
  } finally {
    catalogLoading = false;
  }
}

/** 要扫描的 ECU 总数（只扫有定制项的） */
export function customizationScanTotal(): number {
  const c = toyotaData.catalog;
  return c ? ecusWithSettings(c.ecus, c.settings).length : 0;
}

/** 会话没了（退出或空闲超时）：没扫完的结果不能当成扫描结果显示 */
function isSessionGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409;
}

/** 逐个 ECU 读定制项当前值。单个 ECU 失败只记下来继续；会话没了（409）就中止，这次扫描作废（回到 idle）。 */
export async function scanCustomizations(): Promise<void> {
  const catalog = toyotaData.catalog;
  const c = toyotaData.customization;
  if (!catalog || c.state === "scanning") return;
  c.state = "scanning";
  c.statuses = [];
  c.unreachable = [];
  c.values = {};
  for (const ecu of ecusWithSettings(catalog.ecus, catalog.settings)) {
    try {
      const r = await api.toyotaScanCustomizations(ecu.id);
      c.statuses.push(r.status);
      toyotaData.ecuAnswered[ecu.id] = r.status === "ok";
      if (r.status !== "ok") c.unreachable.push(ecu);
      for (const it of r.items) c.values[it.key] = { value: it.value, raw: it.raw };
    } catch (e) {
      if (isSessionGone(e)) {
        c.state = "idle";
        return;
      }
      c.statuses.push("offline");
      toyotaData.ecuAnswered[ecu.id] = false;
      c.unreachable.push(ecu);
    }
  }
  c.state = "done";
}

/** 逐个 ECU 读故障码（全部 41 个）。失败的 ECU 记为无应答继续；会话没了（409）就中止，这次扫描作废（回到 idle）。 */
export async function scanDtcs(): Promise<void> {
  const catalog = toyotaData.catalog;
  const d = toyotaData.dtc;
  if (!catalog || d.state === "scanning") return;
  d.state = "scanning";
  d.scans = [];
  for (const ecu of catalog.ecus) {
    try {
      d.scans.push(await api.toyotaScanDtc(ecu.id));
    } catch (e) {
      if (isSessionGone(e)) {
        d.state = "idle";
        return;
      }
      d.scans.push({ ecu: ecu.id, status: "offline", protocol: ecu.protocol, dtcs: [] });
    }
    toyotaData.ecuAnswered[ecu.id] = d.scans[d.scans.length - 1].status === "ok";
  }
  d.state = "done";
}

/** 清除成功的 ECU 先在本地清空列表；是否真的清掉、故障是否还在，要重新扫描才知道 */
export async function clearDtcs(ecuIds: string[]): Promise<{ ecu: string; ok: boolean; error?: string }[]> {
  const { results } = await api.toyotaClearDtc(ecuIds);
  for (const r of results) {
    const scan = toyotaData.dtc.scans.find((s) => s.ecu === r.ecu);
    if (r.ok && scan) scan.dtcs = [];
  }
  return results;
}

/** 写入 / 撤销后 ECU 里整段数据变成 afterRaw：同一 ECU、同一 DID / localId 的兄弟设置项一并更新，
 *  否则它们的 raw 过期，下次写入会被后端当作数据已变（409）。 */
export function applyWriteResult(setting: ToyotaSetting, afterRaw: string): void {
  const c = toyotaData.customization;
  Object.assign(c.values, settingValuesAfterRawChange(toyotaData.catalog?.settings ?? [], setting, afterRaw, c.values));
}

/** 扫描结果作废：写入以外的途径（恢复备份）改了车上的值，下次进定制页重新扫描 */
export function invalidateCustomizationScan(): void {
  toyotaData.customization.state = "idle";
}

export function resetScanData(): void {
  toyotaData.ecuAnswered = {};
  const c = toyotaData.customization;
  c.state = "idle";
  c.statuses = [];
  c.unreachable = [];
  c.values = {};
  toyotaData.dtc.state = "idle";
  toyotaData.dtc.scans = [];
  toyotaData.battery.analysis = null;
}
