import { describe, expect, it } from "vitest";
import {
  countdownText, ecusWithSettings, errorText, formatNumber, hexDiff, idleRemainingS, liveValueText, mileageText,
  dtcDescription, liveCards, dtcScanState, dtcStatusTags, dtcSummary, dtcSummaryText, ecuProtocolText, groupDtcs, onlineEcuText, previewRaw,
  restoreToast, scanTally, settingValueFromRaw, settingValuesAfterRawChange, sortEcusByDtcCount, sessionView, settingValueLabel,
} from "../src/lib/toyota";
import type { ToyotaLiveItem, ToyotaDtc, ToyotaDtcScan, ToyotaEcu, ToyotaLiveValue, ToyotaSession, ToyotaSetting } from "../src/lib/schema";
import { ApiError } from "../src/lib/api";

function session(over: Partial<ToyotaSession> = {}): ToyotaSession {
  return { state: "idle", idleTimeoutS: 900, ...over };
}

describe("hexDiff", () => {
  it("相同数据没有差异", () => {
    const d = hexDiff("00 11", "0011");
    expect(d.before.map((b) => b.changed)).toEqual([false, false]);
    expect(d.after.map((b) => b.changed)).toEqual([false, false]);
  });

  it("逐字节标出不同的位置，并统一为大写两位", () => {
    const d = hexDiff("00ff10", "00fe10");
    expect(d.before.map((b) => b.byte)).toEqual(["00", "FF", "10"]);
    expect(d.after.map((b) => b.byte)).toEqual(["00", "FE", "10"]);
    expect(d.after.map((b) => b.changed)).toEqual([false, true, false]);
    expect(d.before.map((b) => b.changed)).toEqual([false, true, false]);
  });

  it("长度不同：多出来的字节算差异", () => {
    const d = hexDiff("0001", "000102");
    expect(d.before).toHaveLength(2);
    expect(d.after.map((b) => b.changed)).toEqual([false, false, true]);
  });

  it("空串得到空数组", () => {
    expect(hexDiff("", "")).toEqual({ before: [], after: [] });
  });
});

describe("sessionView", () => {
  it("idle：只能进入，标签页禁用", () => {
    const v = sessionView(session());
    expect(v).toMatchObject({ label: "未进入", canEnter: true, canExit: false, canUseDiagnostics: false });
  });

  it("entering：都不可点", () => {
    expect(sessionView(session({ state: "entering" }))).toMatchObject({
      label: "进入中", canEnter: false, canExit: false, canUseDiagnostics: false,
    });
  });

  it("active：可退出，标签页可用", () => {
    expect(sessionView(session({ state: "active" }))).toMatchObject({
      label: "诊断中", canEnter: false, canExit: true, canUseDiagnostics: true,
    });
  });

  it("idle 带 error：显示上次失败原因，允许重试进入和退出清理", () => {
    const v = sessionView(session({ state: "idle", error: "pandad 未停" }));
    expect(v).toMatchObject({ label: "未进入", tone: "danger", canEnter: true, canExit: true, canUseDiagnostics: false, detail: "pandad 未停" });
  });

  it("active 不显示 error", () => {
    expect(sessionView(session({ state: "active", error: "旧错误" })).detail).toBe("");
  });

  it("没有会话数据时按未进入处理但不能操作", () => {
    expect(sessionView(null)).toMatchObject({ canEnter: false, canExit: false, canUseDiagnostics: false });
  });
});

describe("idleRemainingS / countdownText", () => {
  it("剩余 = 设备给的 idleRemainingS 减去本地已过去的秒数，不小于 0", () => {
    const s = session({ state: "active", idleRemainingS: 800 });
    expect(idleRemainingS(s, 0)).toBe(800);
    expect(idleRemainingS(s, 100)).toBe(700);
    expect(idleRemainingS(s, 5000)).toBe(0);
  });

  it("非 active 或缺 idleRemainingS 返回 null", () => {
    expect(idleRemainingS(session({ state: "idle", idleRemainingS: 5 }), 1)).toBeNull();
    expect(idleRemainingS(session({ state: "active" }), 1)).toBeNull();
    expect(idleRemainingS(null, 1)).toBeNull();
  });

  it("格式化为 mm:ss", () => {
    expect(countdownText(0)).toBe("00:00");
    expect(countdownText(65)).toBe("01:05");
    expect(countdownText(899.4)).toBe("14:59");
  });
});

describe("errorText", () => {
  it("ApiError 取服务端的 error 文案", () => {
    expect(errorText(new ApiError("请先进入诊断模式", 409))).toBe("请先进入诊断模式");
  });
  it("普通 Error 取 message，其他转字符串", () => {
    expect(errorText(new Error("boom"))).toBe("boom");
    expect(errorText("x")).toBe("x");
  });
});

describe("formatNumber", () => {
  it("按指定小数位四舍五入，补零", () => {
    expect(formatNumber(14.248, 2)).toBe("14.25");
    expect(formatNumber(14.2, 2)).toBe("14.20");
    expect(formatNumber(0, 1)).toBe("0.0");
  });

  it("整数位带千分位", () => {
    expect(formatNumber(13875, 0)).toBe("13,875");
  });
});

describe("liveValueText", () => {
  it("用后端给的 text 加单位", () => {
    expect(liveValueText({ key: "k", value: 14.248, text: "14.25" }, "V")).toBe("14.25 V");
    expect(liveValueText({ key: "k", value: 0, text: "0" })).toBe("0");
  });

  it("没读到或该项出错显示 —", () => {
    expect(liveValueText(undefined, "V")).toBe("—");
    expect(liveValueText({ key: "k", value: null, text: "", error: "无响应" }, "V")).toBe("—");
  });
});

describe("mileageText", () => {
  const mileage: ToyotaLiveValue = { key: "m", value: 13875, text: "13875" };

  it("里程千分位 + 单位项的文字", () => {
    expect(mileageText(mileage, { key: "u", value: 2, text: "英里" })).toBe("13,875 英里");
  });

  it("单位项没读到时按公里显示", () => {
    expect(mileageText(mileage, undefined)).toBe("13,875 公里");
  });

  it("里程没读到显示 —", () => {
    expect(mileageText(undefined, { key: "u", value: 1, text: "公里" })).toBe("—");
    expect(mileageText({ key: "m", value: null, text: "", error: "x" }, undefined)).toBe("—");
  });
});

function setting(over: Partial<ToyotaSetting>): ToyotaSetting {
  return {
    key: "k", category: "C", name: "n", ecu: "750/40", did: "2270", localId: null, byteIndex: 0, mask: "FF",
    requiresBitSupportCheck: false, options: [], numeric: null, popular: false, ...over,
  };
}

describe("settingValueLabel", () => {
  const onOff = setting({ options: [{ value: 1, label: "开启" }, { value: 0, label: "关闭" }] });

  it("选项型显示选项文案", () => {
    expect(settingValueLabel(onOff, 1)).toBe("开启");
    expect(settingValueLabel(onOff, 0)).toBe("关闭");
  });

  it("读到的值不在选项里：显示原始值，不猜", () => {
    expect(settingValueLabel(onOff, 3)).toBe("其他（原始值 3）");
  });

  it("数值型直接显示数字", () => {
    expect(settingValueLabel(setting({ numeric: { min: 1, max: 11 } }), 6)).toBe("6");
  });

  it("没读到显示 —", () => {
    expect(settingValueLabel(onOff, null)).toBe("—");
    expect(settingValueLabel(onOff, undefined)).toBe("—");
  });
});

describe("ecusWithSettings", () => {
  const ecu = (id: string): ToyotaEcu => ({ id, tx: id.slice(0, 3), sub: null, name: id, protocol: "uds" });

  it("只留下有定制项的 ECU，保持目录顺序", () => {
    const ecus = [ecu("7B0"), ecu("750/40"), ecu("7C4"), ecu("750/90")];
    const settings = [setting({ ecu: "750/90" }), setting({ ecu: "750/40" }), setting({ ecu: "750/40" })];
    expect(ecusWithSettings(ecus, settings).map((e) => e.id)).toEqual(["750/40", "750/90"]);
  });
});

describe("scanTally", () => {
  it("进度 = 已完成 / 总数，不论各 ECU 的状态", () => {
    expect(scanTally(["ok", "offline", "malformed", "ok"], 8)).toEqual({ done: 4, total: 8, percent: 50 });
  });

  it("还没开始：全 0", () => {
    expect(scanTally([], 14)).toEqual({ done: 0, total: 14, percent: 0 });
  });

  it("总数为 0 时进度为 0，不出现 NaN", () => {
    expect(scanTally([], 0).percent).toBe(0);
  });
});

describe("previewRaw", () => {
  it("只改掩码覆盖的位，其余位和字节原样保留", () => {
    // 0x5F = 0101 1111，掩码 0x30 = 0011 0000，写入 2 → 位 5..4 = 10 → 0110 1111 = 0x6F
    expect(previewRaw("0A5F", 1, "30", 2)).toBe("0A6F");
  });

  it("整字节掩码直接替换该字节", () => {
    expect(previewRaw("0A5F", 0, "FF", 6)).toBe("065F");
  });

  it("输出统一大写、不带空格", () => {
    expect(previewRaw("0a 5f", 1, "0F", 0)).toBe("0A50");
  });

  it("字节下标越界时原样返回", () => {
    expect(previewRaw("0A", 3, "FF", 1)).toBe("0A");
  });
});

describe("dtcStatusTags", () => {
  const labels = (status: number | null) => dtcStatusTags(status).map((t) => t.label);

  it("已确认且非当前 = 历史（0x28：bit3 + bit5）", () => {
    expect(labels(0x28)).toEqual(["历史"]);
  });

  it("当前 + 已确认（0x09：bit0 + bit3）", () => {
    expect(labels(0x09)).toEqual(["当前故障", "已确认"]);
  });

  it("待定（0x04：bit2）", () => {
    expect(labels(0x04)).toEqual(["待定"]);
  });

  it("全部置位（0x8D：bit0/2/3/7）", () => {
    expect(labels(0x8d)).toEqual(["当前故障", "已确认", "待定", "请求点亮故障灯"]);
  });

  it("没有任何相关位：无标签", () => {
    expect(labels(0)).toEqual([]);
  });

  it("CAN 型 ECU 的码没有状态：显示已确认", () => {
    expect(labels(null)).toEqual(["已确认"]);
  });

  it("当前故障和待定用警示色，历史用中性色", () => {
    expect(dtcStatusTags(0x01)[0].tone).toBe("danger");
    expect(dtcStatusTags(0x04)[0].tone).toBe("warn");
    expect(dtcStatusTags(0x28)[0].tone).toBe("muted");
  });
});

describe("groupDtcs", () => {
  const dtc = (code: string, status: number | null): ToyotaDtc => ({ code, status, text: "" });

  it("FTB 87 且非当前的码归入历史通信丢失，其余留在原列表", () => {
    const g = groupDtcs([dtc("U0131-87", 0x28), dtc("U0163-87", 0x08), dtc("B1234-13", 0x28), dtc("U0100-87", 0x09)]);
    expect(g.history.map((d) => d.code)).toEqual(["U0131-87", "U0163-87"]);
    expect(g.shown.map((d) => d.code)).toEqual(["B1234-13", "U0100-87"]);
  });

  it("没有 FTB 的码（CAN 型）不分组", () => {
    const g = groupDtcs([dtc("B2799", null)]);
    expect(g.history).toEqual([]);
    expect(g.shown).toHaveLength(1);
  });
});

describe("dtcDescription", () => {
  it("有说明用说明，没有显示暂无中文说明", () => {
    expect(dtcDescription({ code: "U0131-87", status: 0x28, text: "与动力转向控制模块通信丢失（报文丢失）" }))
      .toBe("与动力转向控制模块通信丢失（报文丢失）");
    expect(dtcDescription({ code: "P0A80-00", status: 0x08, text: "" })).toBe("暂无中文说明");
  });
});

describe("dtcSummary / sortEcusByDtcCount / dtcSummaryText", () => {
  const scan = (ecu: string, codes: string[]): ToyotaDtcScan => ({
    ecu, status: "ok", protocol: "uds", dtcs: codes.map((code) => ({ code, status: 0x28, text: "" })),
  });
  const scans = [scan("7B0", []), scan("7C4", ["U0131-87", "U0163-87"]), scan("750/40", ["B1000-00"])];

  it("统计扫描 ECU 数、有故障 ECU 数、故障码总数", () => {
    expect(dtcSummary(scans)).toEqual({ ecuCount: 3, faultyEcuCount: 2, codeCount: 3, rejectedEcuCount: 0 });
  });

  it("按故障数降序，同数保持目录顺序", () => {
    const ecus = ["7B0", "7C4", "750/40", "7C0"].map((id) => ({ id }));
    expect(sortEcusByDtcCount(ecus, scans).map((e) => e.id)).toEqual(["7C4", "750/40", "7B0", "7C0"]);
  });

  it("首页摘要三种情况", () => {
    expect(dtcSummaryText(null)).toBe("尚未扫描");
    expect(dtcSummaryText([scan("7B0", [])])).toBe("上次扫描：未发现故障码");
    expect(dtcSummaryText(scans)).toBe("上次扫描：2 个 ECU 共 3 条故障码");
  });

  it("拒绝读码的 ECU 不算无故障：单独计数并写进摘要", () => {
    const rejected: ToyotaDtcScan = { ecu: "7C0", status: "ok", protocol: "uds", dtcs: [], readError: "NRC 11" };
    expect(dtcSummary([...scans, rejected])).toMatchObject({ faultyEcuCount: 2, rejectedEcuCount: 1 });
    expect(dtcSummaryText([scan("7B0", []), rejected])).toBe("上次扫描：未发现故障码（1 个 ECU 拒绝读码）");
    expect(dtcSummaryText([...scans, rejected])).toBe("上次扫描：2 个 ECU 共 3 条故障码（1 个 ECU 拒绝读码）");
  });
});

describe("liveCards", () => {
  const item = (key: string, name: string, over: Partial<ToyotaLiveItem> = {}): ToyotaLiveItem =>
    ({ key, name, unit: "", options: null, needsEngineRunning: false, ...over });
  const val = (key: string, value: number | null, text: string, error?: string): ToyotaLiveValue => ({ key, value, text, error });

  it("数值项：大字 text + 小字单位，需发动机运转的带标记", () => {
    const cards = liveCards(
      [item("rpm", "发动机转速", { unit: "rpm", needsEngineRunning: true }), item("v", "12V", { unit: "V" })],
      [val("v", 14.248, "14.25"), val("rpm", 0, "0")],
    );
    expect(cards).toEqual([
      { key: "rpm", name: "发动机转速", value: "0", unit: "rpm", needsEngineRunning: true },
      { key: "v", name: "12V", value: "14.25", unit: "V", needsEngineRunning: false },
    ]);
  });

  it("里程单位并进里程卡，不单独成卡", () => {
    const cards = liveCards(
      [item("car_tool_mileage", "总里程（仪表读数）"), item("car_tool_mileage_unit", "总里程单位（1 = 公里）")],
      [val("car_tool_mileage", 13875, "13875"), val("car_tool_mileage_unit", 1, "公里")],
    );
    expect(cards).toEqual([
      { key: "car_tool_mileage", name: "总里程（仪表读数）", value: "13,875", unit: "公里", needsEngineRunning: false },
    ]);
  });

  it("该项读取出错或没返回：显示 —，不带单位", () => {
    const cards = liveCards([item("a", "A", { unit: "V" }), item("b", "B", { unit: "°C" })], [val("a", null, "", "无响应")]);
    expect(cards.map((c) => [c.value, c.unit])).toEqual([["—", ""], ["—", ""]]);
  });
});

// 向量来自后端 tests/test_toyota_coding.py::TestValueBits，两端取值语义必须一致
describe("settingValueFromRaw", () => {
  it("取 byteIndex 字节里掩码覆盖的位并移到最低位", () => {
    expect(settingValueFromRaw("00b4", 1, "30")).toBe(3);
  });

  it("整字节掩码直接取该字节", () => {
    expect(settingValueFromRaw("0A5F", 1, "FF")).toBe(0x5f);
  });

  it("raw 带空格、大小写混用也行", () => {
    expect(settingValueFromRaw("00 B4", 1, "30")).toBe(3);
  });

  it("多字节掩码：只有 byteIndex 字节里的位参与，移位按最低置位位置", () => {
    expect(settingValueFromRaw("00b4", 1, "0F30")).toBe(3);
  });

  it("数据太短：没有这个字节就取不到（null）", () => {
    expect(settingValueFromRaw("01", 1, "FF")).toBeNull();
  });
});

describe("settingValuesAfterRawChange", () => {
  // 750/B5 DID 2009 被两项共用：byte 0 的高低半字节
  const high = setting({ key: "high", ecu: "750/B5", did: "2009", byteIndex: 0, mask: "F0" });
  const low = setting({ key: "low", ecu: "750/B5", did: "2009", byteIndex: 0, mask: "0F" });
  const otherDid = setting({ key: "otherDid", ecu: "750/B5", did: "200A", byteIndex: 0, mask: "FF" });
  const otherEcu = setting({ key: "otherEcu", ecu: "750/40", did: "2009", byteIndex: 0, mask: "FF" });
  const all = [high, low, otherDid, otherEcu];
  const scanned = {
    high: { value: 1, raw: "12" }, low: { value: 2, raw: "12" },
    otherDid: { value: 9, raw: "09" }, otherEcu: { value: 8, raw: "08" },
  };

  it("同 ECU 同 DID 的所有已扫描项拿到新 raw 和据此重算的值，别的 DID / ECU 不动", () => {
    expect(settingValuesAfterRawChange(all, high, "52", scanned)).toEqual({
      high: { value: 5, raw: "52" }, low: { value: 2, raw: "52" },
    });
  });

  it("没扫描过的兄弟项不凭空出现", () => {
    expect(settingValuesAfterRawChange(all, high, "52", { high: scanned.high })).toEqual({ high: { value: 5, raw: "52" } });
  });

  it("Toyota CAN 按 localId 分组", () => {
    const a = setting({ key: "a", ecu: "7C0", did: null, localId: "21", byteIndex: 0, mask: "FF" });
    const b = setting({ key: "b", ecu: "7C0", did: null, localId: "21", byteIndex: 1, mask: "0F" });
    const c = setting({ key: "c", ecu: "7C0", did: null, localId: "22", byteIndex: 0, mask: "FF" });
    const known = { a: { value: 0, raw: "0000" }, b: { value: 0, raw: "0000" }, c: { value: 7, raw: "07" } };
    expect(settingValuesAfterRawChange([a, b, c], a, "0A03", known)).toEqual({
      a: { value: 10, raw: "0A03" }, b: { value: 3, raw: "0A03" },
    });
  });

  it("新 raw 太短取不到的兄弟项保持原样（不写入）", () => {
    const tail = setting({ key: "tail", ecu: "750/B5", did: "2009", byteIndex: 3, mask: "FF" });
    const known = { ...scanned, tail: { value: 4, raw: "12000004" } };
    expect(settingValuesAfterRawChange([high, tail], high, "52", known)).toEqual({ high: { value: 5, raw: "52" } });
  });
});

describe("dtcScanState", () => {
  const scan = (over: Partial<ToyotaDtcScan>): ToyotaDtcScan => ({ ecu: "7B0", status: "ok", protocol: "uds", dtcs: [], ...over });

  it("有故障码 → faulty", () => {
    expect(dtcScanState(scan({ dtcs: [{ code: "U0131-87", status: 0x28, text: "" }] }))).toBe("faulty");
  });

  it("应答正常且没有码 → clear", () => {
    expect(dtcScanState(scan({}))).toBe("clear");
  });

  it("应答正常但 ECU 拒绝读码（readError）→ rejected，不能当成无故障", () => {
    expect(dtcScanState(scan({ readError: "NRC 11" }))).toBe("rejected");
  });

  it("无应答 / 应答异常沿用扫描状态", () => {
    expect(dtcScanState(scan({ status: "offline", readError: "x" }))).toBe("offline");
    expect(dtcScanState(scan({ status: "malformed" }))).toBe("malformed");
  });

  it("没扫到（undefined）→ unscanned", () => {
    expect(dtcScanState(undefined)).toBe("unscanned");
  });
});

describe("onlineEcuText", () => {
  it("没探测过：—", () => {
    expect(onlineEcuText({})).toBe("—");
  });

  it("在线数 / 已探测数", () => {
    expect(onlineEcuText({ "7B0": true, "7C4": false, "750/40": true })).toBe("2 / 3");
  });
});

describe("ecuProtocolText", () => {
  it("两种协议各一句话", () => {
    expect(ecuProtocolText("uds")).toBe("UDS");
    expect(ecuProtocolText("toyota_can")).toBe("Toyota CAN（旧式）");
  });
});

describe("restoreToast", () => {
  it("回读校验通过：ok，带项目名与恢复后的值", () => {
    expect(restoreToast("开门提醒", "关闭", true)).toEqual({ message: "已恢复「开门提醒」→ 关闭", tone: "ok" });
  });

  it("回读校验未通过：warn", () => {
    expect(restoreToast("开门提醒", "关闭", false)).toEqual({ message: "已恢复，但回读校验未通过", tone: "warn" });
  });
});
