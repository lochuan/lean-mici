import { describe, expect, it } from "vitest";
import {
  PID_SELECTION_LIMIT, countdownText, errorText, filterPidGroups, groupCustomizations, hexDiff,
  idleRemainingS, scanEcuText, scanSummary, sessionView, togglePid,
} from "../src/lib/toyota";
import type { ToyotaCustomizationItem, ToyotaPidGroup, ToyotaScanEcu, ToyotaSession } from "../src/lib/schema";
import { ApiError } from "../src/lib/api";

function item(over: Partial<ToyotaCustomizationItem>): ToyotaCustomizationItem {
  return {
    uid: 1, group: "车身", name: "x", ecu: "750/40 车身控制", options: [], current: null, raw: "", writable: true, ...over,
  };
}

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

describe("groupCustomizations", () => {
  it("按 group 分组，组内按名称再按 uid 排序", () => {
    const groups = groupCustomizations([
      item({ uid: 3, group: "灯光", name: "b" }),
      item({ uid: 2, group: "车身", name: "b" }),
      item({ uid: 1, group: "车身", name: "b" }),
      item({ uid: 4, group: "车身", name: "a" }),
    ]);
    expect(groups.map((g) => g.group)).toEqual(["灯光", "车身"].sort((a, b) => a.localeCompare(b, "zh")));
    const body = groups.find((g) => g.group === "车身")!;
    expect(body.items.map((i) => i.uid)).toEqual([4, 1, 2]);
  });

  it("空列表得到空数组", () => {
    expect(groupCustomizations([])).toEqual([]);
  });
});

describe("togglePid", () => {
  it("未选中则加入，已选中则移除", () => {
    expect(togglePid([1], 2)).toEqual({ selected: [1, 2], overLimit: false });
    expect(togglePid([1, 2], 1)).toEqual({ selected: [2], overLimit: false });
  });

  it("达到上限后不再加入并提示", () => {
    const full = Array.from({ length: PID_SELECTION_LIMIT }, (_, i) => i);
    const r = togglePid(full, 999);
    expect(r.selected).toEqual(full);
    expect(r.overLimit).toBe(true);
  });

  it("满额时仍可取消勾选", () => {
    const full = Array.from({ length: PID_SELECTION_LIMIT }, (_, i) => i);
    expect(togglePid(full, 0).selected).toHaveLength(PID_SELECTION_LIMIT - 1);
  });

  it("上限是 40", () => {
    expect(PID_SELECTION_LIMIT).toBe(40);
  });
});

describe("filterPidGroups", () => {
  const groups: ToyotaPidGroup[] = [
    { ecu: "7E0 发动机", pids: [{ id: 1, name: "Coolant Temp", unit: "°C" }, { id: 2, name: "RPM", unit: "rpm" }] },
    { ecu: "7B0 ABS", pids: [{ id: 3, name: "车速", unit: "km/h" }] },
  ];

  it("空查询原样返回", () => {
    expect(filterPidGroups(groups, "  ")).toEqual(groups);
  });

  it("不区分大小写匹配名称，丢弃没有命中的 ECU 组", () => {
    const r = filterPidGroups(groups, "coolant");
    expect(r).toEqual([{ ecu: "7E0 发动机", pids: [{ id: 1, name: "Coolant Temp", unit: "°C" }] }]);
  });

  it("中文名称可匹配", () => {
    expect(filterPidGroups(groups, "车速").map((g) => g.ecu)).toEqual(["7B0 ABS"]);
  });
});

describe("sessionView", () => {
  it("idle：只能进入，标签页禁用", () => {
    const v = sessionView(session());
    expect(v).toMatchObject({ label: "未进入", canEnter: true, canExit: false, tabsEnabled: false });
  });

  it("entering：都不可点", () => {
    expect(sessionView(session({ state: "entering" }))).toMatchObject({
      label: "进入中", canEnter: false, canExit: false, tabsEnabled: false,
    });
  });

  it("active：可退出，标签页可用", () => {
    expect(sessionView(session({ state: "active" }))).toMatchObject({
      label: "诊断中", canEnter: false, canExit: true, tabsEnabled: true,
    });
  });

  it("idle 带 error：显示上次失败原因，允许重试进入和退出清理", () => {
    const v = sessionView(session({ state: "idle", error: "pandad 未停" }));
    expect(v).toMatchObject({ label: "未进入", tone: "danger", canEnter: true, canExit: true, tabsEnabled: false, detail: "pandad 未停" });
  });

  it("active 不显示 error", () => {
    expect(sessionView(session({ state: "active", error: "旧错误" })).detail).toBe("");
  });

  it("没有会话数据时按未进入处理但不能操作", () => {
    expect(sessionView(null)).toMatchObject({ canEnter: false, canExit: false, tabsEnabled: false });
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

describe("scanEcuText / scanSummary", () => {
  const ecu = (over: Partial<ToyotaScanEcu>): ToyotaScanEcu =>
    ({ ecu: "750/40 车身控制", status: "ok", total: 10, supported: 0, read: 0, ...over });

  it("每种状态都说明卡在哪一步", () => {
    expect(scanEcuText(ecu({ status: "offline" }))).toBe("无响应（不在线或未上电）");
    expect(scanEcuText(ecu({ status: "silent" }))).toBe("在线，但不应答 A8 03");
    expect(scanEcuText(ecu({ status: "rejected", nrc: "22" }))).toBe("拒绝 A8 03：NRC 22（条件不满足）");
    expect(scanEcuText(ecu({ status: "rejected", nrc: "9A" }))).toBe("拒绝 A8 03：NRC 9A");
    expect(scanEcuText(ecu({ status: "empty", response: "e803" }))).toBe("A8 03 应答里没有掩码：e803");
    expect(scanEcuText(ecu({ status: "malformed" }))).toBe("应答异常（ISO-TP 帧错乱），已跳过");
    expect(scanEcuText(ecu({ status: "ok" }))).toBe("掩码表不含数据库里的 10 项");
    expect(scanEcuText(ecu({ supported: 4, read: 3, readError: "nrc 22" }))).toBe("支持 4/10 项，读到 3 项（读值失败：nrc 22）");
  });

  it("汇总在线与提供定制项的 ECU 数", () => {
    const ecus = [ecu({ status: "offline" }), ecu({ status: "rejected", nrc: "22" }), ecu({ supported: 2, read: 2 })];
    expect(scanSummary(ecus)).toBe("3 个 ECU 地址，2 个在线，1 个提供了定制项");
  });
});
