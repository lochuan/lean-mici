import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/lib/api";
import type { ToyotaCatalog, ToyotaEcu, ToyotaSetting, ToyotaWriteResult } from "../src/lib/schema";
import { applyWriteResult, resetScanData, scanCustomizations, scanDtcs, toyotaData } from "../src/lib/toyotaData";

function setting(over: Partial<ToyotaSetting>): ToyotaSetting {
  return {
    key: "k", category: "C", name: "n", ecu: "750/B5", did: "2009", localId: null, byteIndex: 0, mask: "FF",
    requiresBitSupportCheck: false, options: [], numeric: null, popular: false, ...over,
  };
}

function writeResult(before: string, after: string): ToyotaWriteResult {
  return { key: "k", value: 0, verified: true, backupId: "b", before, after };
}

// 750/B5 DID 2009 被两项共用：byte 0 的高 / 低半字节
const highNibble = setting({ key: "high", mask: "F0" });
const lowNibble = setting({ key: "low", mask: "0F" });

beforeEach(() => {
  toyotaData.catalog = { ecus: [], categories: [], settings: [highNibble, lowNibble], liveTools: [], live: [], battery: { ecu: "", blocks: [] } } as ToyotaCatalog;
  resetScanData();
  toyotaData.customization.values = { high: { value: 1, raw: "12" }, low: { value: 2, raw: "12" } };
});

describe("applyWriteResult", () => {
  it("同 DID 两项先后写入：第二项看到的 raw 已含第一次写入", () => {
    applyWriteResult(highNibble, "52");
    expect(toyotaData.customization.values.low.raw).toBe("52");
    applyWriteResult(lowNibble, "57");
    expect(toyotaData.customization.values.high).toEqual({ value: 5, raw: "57" });
    expect(toyotaData.customization.values.low).toEqual({ value: 7, raw: "57" });
  });

  it("撤销（按 restore 返回的 after 更新）后兄弟项值随之恢复", () => {
    applyWriteResult(highNibble, "52");
    const restored = writeResult("52", "12");
    applyWriteResult(highNibble, restored.after);
    expect(toyotaData.customization.values).toEqual({ high: { value: 1, raw: "12" }, low: { value: 2, raw: "12" } });
  });
});

describe("resetScanData", () => {
  it("退出诊断时清掉 ECU 应答记录", () => {
    toyotaData.ecuAnswered["7B0"] = true;
    resetScanData();
    expect(toyotaData.ecuAnswered).toEqual({});
  });
});

describe("扫描途中会话没了（409）", () => {
  const ecus: ToyotaEcu[] = ["750/B5", "750/40"].map((id) => ({ id, tx: "750", sub: id.slice(4), name: id, protocol: "uds" }));
  const sessionGone = () => Promise.reject(new ApiError("诊断会话未开启", 409));

  beforeEach(() => {
    toyotaData.catalog!.ecus = ecus;
    toyotaData.catalog!.settings = [highNibble, setting({ key: "body", ecu: "750/40" })];
  });
  afterEach(() => vi.restoreAllMocks());

  it("故障码扫描作废回到 idle，不把没扫到的 ECU 当成无故障", async () => {
    vi.spyOn(api, "toyotaScanDtc")
      .mockResolvedValueOnce({ ecu: "750/B5", status: "ok", protocol: "uds", dtcs: [] })
      .mockImplementationOnce(sessionGone);
    await scanDtcs();
    expect(toyotaData.dtc.state).toBe("idle");
  });

  it("定制扫描作废回到 idle", async () => {
    vi.spyOn(api, "toyotaScanCustomizations")
      .mockResolvedValueOnce({ ecu: "750/B5", status: "ok", items: [] } as never)
      .mockImplementationOnce(sessionGone);
    await scanCustomizations();
    expect(toyotaData.customization.state).toBe("idle");
  });
});
