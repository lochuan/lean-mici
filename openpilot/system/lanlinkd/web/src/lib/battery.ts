/** 动力电池判定：12 块电池的电压与内阻 → 健康 / 注意 / 异常。
 *  阈值移植自用户认可的原型（没有丰田官方阈值，按块间差异判断）。 */
import type { ToyotaBatteryBlock, ToyotaLiveValue } from "./schema";
import { formatNumber } from "./toyota";

export type BatteryLevel = "ok" | "warn" | "danger";

export interface BatteryBlockReading {
  /** 块号，从 1 开始 */
  n: number;
  /** 模组数：半块 2、整块 4 */
  modules: number;
  voltage: number;
  /** mΩ */
  resistance: number;
}

export interface BatteryBlock extends BatteryBlockReading {
  /** 折算到每 2 模组的电压：半块不变，整块 ÷2 */
  v2: number;
}

export interface BatteryAnalysis {
  blocks: BatteryBlock[];
  total: number;
  max: BatteryBlock;
  min: BatteryBlock;
  /** 每 2 模组电压的最大 − 最小 */
  diff: number;
  rAvg: number;
  rMax: BatteryBlock;
  level: BatteryLevel;
  why: string;
  /** 内阻比同类块（整块 / 半块分开）平均高 20% 以上的块 */
  highR: BatteryBlock[];
  /** 电压比其他块平均低 1 V 以上的块 */
  lowBlocks: BatteryBlock[];
}

const WARN_DIFF_V = 0.3;
const DANGER_DIFF_V = 0.6;
const DANGER_LOW_V = 1;
const HIGH_RESISTANCE_RATIO = 1.2;

export function analyzeBattery(readings: BatteryBlockReading[]): BatteryAnalysis {
  const blocks = readings.map((b) => ({ ...b, v2: b.voltage / (b.modules / 2) }));
  const total = blocks.reduce((s, b) => s + b.voltage, 0);
  const v2Sum = blocks.reduce((s, b) => s + b.v2, 0);
  const max = blocks.reduce((a, b) => (b.v2 > a.v2 ? b : a));
  const min = blocks.reduce((a, b) => (b.v2 < a.v2 ? b : a));
  const diff = max.v2 - min.v2;
  const lowBlocks = blocks.filter((b) => (v2Sum - b.v2) / (blocks.length - 1) - b.v2 >= DANGER_LOW_V);
  const classAvg = (modules: number) => {
    const group = blocks.filter((b) => b.modules === modules);
    return group.reduce((s, b) => s + b.resistance, 0) / group.length;
  };
  const highR = blocks.filter((b) => b.resistance > classAvg(b.modules) * HIGH_RESISTANCE_RATIO);
  const rAvg = blocks.reduce((s, b) => s + b.resistance, 0) / blocks.length;
  const rMax = blocks.reduce((a, b) => (b.resistance > a.resistance ? b : a));
  const diffText = formatNumber(diff, 2);

  let level: BatteryLevel = "ok";
  let why = `12 块电池电压一致（最大差 ${diffText} V），内阻均匀，静置读数未见异常。`;
  if (diff > DANGER_DIFF_V || lowBlocks.length) {
    level = "danger";
    why = lowBlocks.length
      ? `第 ${lowBlocks.map((b) => b.n).join("、")} 块电压比其他块平均低 1 V 以上，建议尽快到店检测。`
      : `块间电压差 ${diffText} V，超过 0.6 V，建议尽快到店检测。`;
  } else if (diff > WARN_DIFF_V || highR.length) {
    level = "warn";
    why = diff > WARN_DIFF_V
      ? `块间电压差 ${diffText} V，略偏大，建议关注并定期复查。`
      : `第 ${highR.map((b) => b.n).join("、")} 块内阻比同类块平均高出 20% 以上，建议关注并定期复查。`;
  }
  return { blocks, total, max, min, diff, rAvg, rMax, level, why, highR, lowBlocks };
}

/** 目录里的块定义 + 实时数据读数 → 判定输入。任一项缺失或读取出错就返回 null（整组不可用）。 */
export function batteryBlocksFromValues(
  battery: { blocks: ToyotaBatteryBlock[] },
  values: ToyotaLiveValue[],
): BatteryBlockReading[] | null {
  const byKey = new Map(values.map((v) => [v.key, v]));
  const readings: BatteryBlockReading[] = [];
  for (const [i, b] of battery.blocks.entries()) {
    const voltage = byKey.get(b.voltageKey);
    const resistance = byKey.get(b.resistanceKey);
    if (voltage?.value == null || voltage.error || resistance?.value == null || resistance.error) return null;
    readings.push({ n: i + 1, modules: b.modules, voltage: voltage.value, resistance: resistance.value });
  }
  return readings;
}
