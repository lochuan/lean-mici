import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn-vue 约定的 class 合并助手 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

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
  if (typeof unit === "string") return unit;
  return metric ? unit.metric : unit.imperial;
}

/** 滑块数值显示：按 step 推断小数位，避免 0.30000000000000004 */
export function formatSliderValue(value: number, step: number | undefined): string {
  const s = step ?? 1;
  if (Number.isInteger(s) && Number.isInteger(value)) return String(value);
  const decimals = (String(s).split(".")[1] ?? "").length;
  return value.toFixed(decimals);
}

export function formatTemp(c: number | undefined): string {
  return c === undefined ? "--" : `${c.toFixed(0)}°C`;
}

/** km/h 或 mph，输入是 m/s */
export function formatSpeed(ms: number | undefined, metric: boolean): string {
  if (ms === undefined) return "--";
  return metric ? `${(ms * 3.6).toFixed(0)} km/h` : `${(ms * 2.236936).toFixed(0)} mph`;
}

export function formatBytes(n: number | undefined): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
