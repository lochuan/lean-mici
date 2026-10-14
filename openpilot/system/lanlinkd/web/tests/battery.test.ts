import { describe, expect, it } from "vitest";
import {
  analyzeBattery, batteryBlocksFromValues, BATTERY_LEVEL_LABEL, DANGER_DIFF_V, WARN_DIFF_V, type BatteryBlockReading,
} from "../src/lib/battery";
import type { ToyotaLiveValue } from "../src/lib/schema";

// Sienna 实车 Ready 下的读数（原型 data.js 的 vReady / rReady），12 块：1、2、11、12 是半块（2 模组）
const REAL: BatteryBlockReading[] = [
  { blockNumber: 1, modules: 2, voltage: 15.249, resistance: 23 },
  { blockNumber: 2, modules: 2, voltage: 15.199, resistance: 22 },
  { blockNumber: 3, modules: 4, voltage: 30.3394, resistance: 22 },
  { blockNumber: 4, modules: 4, voltage: 30.2698, resistance: 22 },
  { blockNumber: 5, modules: 4, voltage: 30.2588, resistance: 22 },
  { blockNumber: 6, modules: 4, voltage: 30.21, resistance: 22 },
  { blockNumber: 7, modules: 4, voltage: 30.2393, resistance: 22 },
  { blockNumber: 8, modules: 4, voltage: 30.2295, resistance: 22 },
  { blockNumber: 9, modules: 4, voltage: 30.2698, resistance: 22 },
  { blockNumber: 10, modules: 4, voltage: 30.3198, resistance: 22 },
  { blockNumber: 11, modules: 2, voltage: 15.199, resistance: 23 },
  { blockNumber: 12, modules: 2, voltage: 15.2197, resistance: 23 },
];

const edit = (blockNumber: number, over: Partial<BatteryBlockReading>) =>
  REAL.map((b) => (b.blockNumber === blockNumber ? { ...b, ...over } : b));

describe("analyzeBattery：实车读数（原型截图 12-battery 上的数字）", () => {
  const a = analyzeBattery(REAL);

  it("总电压 303.0 V，最大电压差 0.14 V，判定健康", () => {
    expect(a.total).toBeCloseTo(303.0, 1);
    expect(a.diff).toBeCloseTo(0.14, 2);
    expect(a.level).toBe("ok");
    expect(a.levelReason).toBe("12 块电池电压一致（最大差 0.14 V），内阻均匀，静置读数未见异常。");
  });

  it("最高块 #1（15.25 V），最低块 #6（15.11 V），整块电压折算到每 2 模组", () => {
    expect(a.max.blockNumber).toBe(1);
    expect(a.max.voltagePerTwoModules).toBeCloseTo(15.25, 2);
    expect(a.min.blockNumber).toBe(6);
    expect(a.min.voltagePerTwoModules).toBeCloseTo(15.11, 2);
  });

  it("内阻平均 22.3、最大 23 mΩ", () => {
    expect(a.resistanceAvg).toBeCloseTo(22.25, 2);
    expect(a.resistanceMaxBlock.resistance).toBe(23);
  });
});

describe("analyzeBattery：异常 / 注意", () => {
  it("第 4 块低 2.4 V（折算后低 1.2 V）：异常，电压差 1.31 V（截图 14-battery-danger）", () => {
    const a = analyzeBattery(edit(4, { voltage: 30.2698 - 2.4 }));
    expect(a.level).toBe("danger");
    expect(a.diff).toBeCloseTo(1.31, 2);
    expect(a.min.blockNumber).toBe(4);
    expect(a.min.voltagePerTwoModules).toBeCloseTo(13.93, 2);
    expect(a.total).toBeCloseTo(300.6, 1);
    expect(a.lowBlocks.map((b) => b.blockNumber)).toEqual([4]);
    expect(a.levelReason).toBe("第 4 块电压比其他块平均低 1 V 以上，建议尽快到店检测。");
  });

  it("压差在 0.3～0.6 V：注意（第 7 块低 0.9 V → 折算后 15.249 − 14.66965 = 0.58 V）", () => {
    const a = analyzeBattery(edit(7, { voltage: 30.2393 - 0.9 }));
    expect(a.level).toBe("warn");
    expect(a.levelReason).toBe("块间电压差 0.58 V，略偏大，建议关注并定期复查。");
  });

  it("压差超过 0.6 V 但没有哪块比平均低 1 V：异常，按压差说明", () => {
    // 半块电压：11 块 15.0 V、1 块 15.75 V，压差 0.75 V；最高块比平均高，没有「低」的块
    const blocks = REAL.map((b) => ({ ...b, modules: 2, voltage: b.blockNumber === 1 ? 15.75 : 15.0 }));
    const a = analyzeBattery(blocks);
    expect(a.level).toBe("danger");
    expect(a.lowBlocks).toEqual([]);
    expect(a.levelReason).toBe("块间电压差 0.75 V，超过 0.6 V，建议尽快到店检测。");
  });

  it("压差 0.5 V：注意", () => {
    const blocks = REAL.map((b) => ({ ...b, modules: 2, voltage: b.blockNumber === 1 ? 15.5 : 15.0 }));
    expect(analyzeBattery(blocks).level).toBe("warn");
  });

  it("整块 / 半块内阻分开比：第 5 块 28 mΩ（同类 8 块均值 22.75，×1.2 = 27.3）→ 注意", () => {
    const a = analyzeBattery(edit(5, { resistance: 28 }));
    expect(a.level).toBe("warn");
    expect(a.highResistanceBlocks.map((b) => b.blockNumber)).toEqual([5]);
    expect(a.levelReason).toBe("第 5 块内阻比同类块平均高出 20% 以上，建议关注并定期复查。");
  });

  it("半块内阻 23 比整块 22 高，但各自和同类比都正常：不误报", () => {
    expect(analyzeBattery(REAL).highResistanceBlocks).toEqual([]);
  });
});

describe("batteryBlocksFromValues", () => {
  const battery = {
    ecu: "7D2",
    blocks: [
      { voltageKey: "v1", resistanceKey: "r1", modules: 2 },
      { voltageKey: "v2", resistanceKey: "r2", modules: 4 },
    ],
  };
  const val = (key: string, value: number | null, error?: string): ToyotaLiveValue => ({ key, value, text: String(value), error });

  it("按目录顺序编号，电压内阻取对应 key 的读数", () => {
    const values = [val("r2", 22), val("v2", 30.25), val("v1", 15.25), val("r1", 23)];
    expect(batteryBlocksFromValues(battery, values)).toEqual([
      { blockNumber: 1, modules: 2, voltage: 15.25, resistance: 23 },
      { blockNumber: 2, modules: 4, voltage: 30.25, resistance: 22 },
    ]);
  });

  it("任一项缺失或读取出错：整组不可用（返回 null）", () => {
    expect(batteryBlocksFromValues(battery, [val("v1", 15.25), val("r1", 23), val("v2", 30.25)])).toBeNull();
    expect(batteryBlocksFromValues(battery, [val("v1", 15.25), val("r1", 23), val("v2", null, "无响应"), val("r2", 22)])).toBeNull();
  });
});

describe("等级文案与阈值常量", () => {
  it("三个等级各有文案和色调，首页和电池页共用", () => {
    expect(BATTERY_LEVEL_LABEL).toEqual({
      ok: { text: "健康", tone: "accent" },
      warn: { text: "注意", tone: "warn" },
      danger: { text: "异常", tone: "danger" },
    });
  });

  it("电压差阈值 0.3 / 0.6 V 导出给页面用", () => {
    expect([WARN_DIFF_V, DANGER_DIFF_V]).toEqual([0.3, 0.6]);
  });
});
