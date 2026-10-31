/** 控件选型：把 schema 的 widget 映射成实际渲染的组件。
 *
 * upstream 的 widget enum 只有 toggle/option/multiple_button/button/info
 * （没有 slider、没有 select），所以真正渲染成什么还要看**数据形状**：
 *
 *   option + options[]        → 下拉
 *   option + min/max/step     → 步进器
 *   multiple_button + 选项少   → 分段按钮
 *   multiple_button + 选项多   → 下拉  ← 见下
 *
 * 最后一条是必须的：`OnroadScreenOffBrightness` 有 23 个选项
 * （自动/熄屏/5%…100%），`InteractivityTimeout` 13 个，横排按钮会直接
 * 溢出屏幕。sunnylink 对这三项正是渲染成 role="combobox" 的下拉
 * （2026-09-12 实测 /dashboard/settings/display），我们保持一致。
 */
import type { Item } from "./schema";

export type Control = "toggle" | "stepper" | "select" | "segmented" | "button" | "info";

/** 超过这个数量就改用下拉。
 *
 * 取 6：实测需要分段按钮的最多 5 个选项（SpeedLimitPolicy），
 * 需要下拉的最少 10 个（OnroadScreenOffTimer），中间留出余量。
 */
export const SEGMENTED_MAX_OPTIONS = 6;

/** 分段按钮的横向预算（px）。
 *
 * 内容列宽 768px，SettingRow 的控件区是 max-w-[55%] ≈ 422px。留一点余量。
 */
export const SEGMENTED_MAX_WIDTH_PX = 400;

/** SegmentedButtons.vue 的实际几何：text-[13px]、px-3、gap-1、容器 p-1。
 *  改那边的样式就要同步改这里，否则选型会失准。 */
const FONT_PX = 13;
const BTN_PADDING_PX = 24; // px-3 左右各 12
const GAP_PX = 4;          // gap-1
const CONTAINER_PX = 8;    // p-1 左右各 4

/** 估算标签渲染宽度。
 *
 * 不能用字数：CJK 是全角（≈ 字号），拉丁字母约 0.55 字号，
 * "车辆信号优先"(6 字 ≈ 78px) 和 "v1.0"(4 字符 ≈ 29px) 差了一倍多。
 * 这是估算而非实测——目的只是把"明显放不下"和"明显放得下"分开。
 */
export function estimateTextPx(text: string, fontPx = FONT_PX): number {
  let px = 0;
  for (const ch of text) {
    // CJK、全角标点、假名等按全角算
    px += /[\u1100-\u11ff\u2e80-\ua4cf\ua960-\ua97f\uac00-\ud7ff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/.test(ch)
      ? fontPx
      : fontPx * 0.55;
  }
  return px;
}

export function estimateSegmentedWidthPx(labels: string[]): number {
  if (labels.length === 0) return 0;
  const text = labels.reduce((n, l) => n + estimateTextPx(l), 0);
  return text + labels.length * BTN_PADDING_PX + (labels.length - 1) * GAP_PX + CONTAINER_PX;
}

export function pickControl(item: Item): Control {
  switch (item.widget) {
    case "toggle":
      return "toggle";
    case "button":
      return "button";
    case "info":
      return "info";

    case "multiple_button":
      return fitsSegmented(item) ? "segmented" : "select";

    case "option": {
      if (item.options?.length) return "select";
      if (item.min !== undefined || item.max !== undefined) return "stepper";
      // 既无选项也无范围：schema 有问题。降级成只读而不是渲染出一个坏控件。
      console.warn(`option 缺少 options/min/max，降级为只读: ${item.key}`);
      return "info";
    }

    default:
      console.warn(`未知 widget «${item.widget}»，降级为只读: ${item.key}`);
      return "info";
  }
}

export function fitsSegmented(item: Item): boolean {
  const opts = item.options ?? [];
  if (opts.length === 0) return false;
  if (opts.length > SEGMENTED_MAX_OPTIONS) return false;
  return estimateSegmentedWidthPx(opts.map((o) => o.label ?? "")) <= SEGMENTED_MAX_WIDTH_PX;
}
