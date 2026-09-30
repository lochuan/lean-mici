import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn-vue 约定的 class 合并助手 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** schema 里少数单位是英文单词而非符号（"second"、"meters"），
 *  在中文界面里紧跟数字显示很别扭（"0second"）。只改显示，不动 schema。 */
const UNIT_LABELS: Record<string, string> = {
  second: "秒",
  seconds: "秒",
  meters: "米",
  meter: "米",
};

/** 单位解析：字符串或 {metric, imperial}，后者跟随 IsMetric 切换。
 *
 * 两种形态都在真实 schema 里出现（CameraOffset 用 "meters"，
 * BlinkerMinLateralControlSpeed 用 {metric:"km/h", imperial:"mph"}）。
 */
export function resolveUnit(
  unit: string | { metric: string; imperial: string } | undefined,
  metric: boolean,
): string {
  if (!unit) return "";
  const raw = typeof unit === "string" ? unit : metric ? unit.metric : unit.imperial;
  return UNIT_LABELS[raw] ?? raw;
}

/** 滑块数值显示：按 step 推断小数位，避免 0.30000000000000004 */
export function formatSliderValue(value: number, step: number | undefined): string {
  const s = step ?? 1;
  if (Number.isInteger(s) && Number.isInteger(value)) return String(value);
  const decimals = (String(s).split(".")[1] ?? "").length;
  return value.toFixed(decimals);
}
