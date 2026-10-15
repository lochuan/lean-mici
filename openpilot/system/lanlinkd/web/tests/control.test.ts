import { describe, expect, it, vi } from "vitest";
import {
  SEGMENTED_MAX_OPTIONS,
  SEGMENTED_MAX_WIDTH_PX,
  estimateSegmentedWidthPx,
  estimateTextPx,
  fitsSegmented,
  pickControl,
} from "../src/lib/control";
import type { Item } from "../src/lib/schema";

const item = (over: Partial<Item>): Item => ({
  key: "K",
  widget: "toggle",
  title: "T",
  ...over,
});

const opts = (labels: string[]) => labels.map((label, value) => ({ value, label }));

describe("widget -> control mapping", () => {
  it("maps the simple widgets straight through", () => {
    expect(pickControl(item({ widget: "toggle" }))).toBe("toggle");
    expect(pickControl(item({ widget: "button" }))).toBe("button");
    expect(pickControl(item({ widget: "info" }))).toBe("info");
  });

  it("splits `option` by data shape, since there is no slider widget upstream", () => {
    expect(pickControl(item({ widget: "option", options: opts(["a", "b"]) }))).toBe("select");
    expect(pickControl(item({ widget: "option", min: 0, max: 10, step: 1 }))).toBe("stepper");
    // 只有 max 也算范围
    expect(pickControl(item({ widget: "option", max: 5 }))).toBe("stepper");
  });

  it("degrades a malformed option to read-only instead of rendering a broken control", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(pickControl(item({ widget: "option" }))).toBe("info");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("multiple_button falls back to a dropdown when it cannot fit", () => {
  it("keeps the real short cases as segmented buttons", () => {
    // 这些是 schema 里真实存在的，横排完全放得下
    expect(pickControl(item({ widget: "multiple_button", options: opts(["激进", "标准", "放松"]) })))
      .toBe("segmented");
    expect(pickControl(item({ widget: "multiple_button", options: opts(["关闭", "仅提示", "警告", "辅助"]) })))
      .toBe("segmented");
    expect(pickControl(item({ widget: "multiple_button", options: opts(["默认", "v1.0", "v0.0"]) })))
      .toBe("segmented");
  });

  it("switches the real overflowing Display items to a dropdown", () => {
    // OnroadScreenOffBrightness：23 个选项（自动/熄屏/5%…100%），
    // 横排会直接冲出屏幕——这是用户报的那个问题
    const brightness = opts([
      "自动（默认）", "自动（暗）", "熄屏",
      ...Array.from({ length: 20 }, (_, i) => `${(i + 1) * 5}%`),
    ]);
    expect(brightness).toHaveLength(23);
    expect(pickControl(item({ widget: "multiple_button", options: brightness }))).toBe("select");

    // InteractivityTimeout: 13 个
    const interactivity = opts(["默认", ...Array.from({ length: 12 }, (_, i) => `${(i + 1) * 10} 秒`)]);
    expect(pickControl(item({ widget: "multiple_button", options: interactivity }))).toBe("select");

    // OnroadScreenOffTimer / ScreenSaverTimeout: 10 个
    const timer = opts(["常亮", "3 秒", "5 秒", "10 秒", "15 秒", "30 秒", "1 分钟", "3 分钟", "5 分钟", "10 分钟"]);
    expect(pickControl(item({ widget: "multiple_button", options: timer }))).toBe("select");
  });

  it("falls back for the real cruise SpeedLimitPolicy row", () => {
    // 巡航页「限速数据来源」：只有 5 个选项，但标签都是 5-6 个汉字，
    // 实际要 ~456px，而控件区只有 ~422px —— 用户报的第二个溢出就是这个。
    const policy = opts(["仅车辆信号", "仅地图数据", "车辆信号优先", "地图数据优先", "综合"]);
    expect(policy.length).toBeLessThanOrEqual(SEGMENTED_MAX_OPTIONS);
    expect(estimateSegmentedWidthPx(policy.map((o) => o.label))).toBeGreaterThan(SEGMENTED_MAX_WIDTH_PX);
    expect(pickControl(item({ widget: "multiple_button", options: policy }))).toBe("select");
  });

  it("measures width in pixels, not characters", () => {
    // 字数相同但宽度差一倍：全角按字号算，拉丁按 0.55 字号算。
    // 早先用字数做阈值，正是这一点让 SpeedLimitPolicy 漏了过去。
    expect(estimateTextPx("车辆信号优先")).toBeGreaterThan(estimateTextPx("v1.0ab"));
    // 4 个短拉丁标签放得下，4 个长中文标签放不下
    expect(fitsSegmented(item({ widget: "multiple_button", options: opts(["v0.0", "v1.0", "v2.0", "v3.0"]) }))).toBe(true);
    expect(fitsSegmented(item({ widget: "multiple_button", options: opts(["车辆信号优先", "地图数据优先", "仅车辆信号源", "仅地图数据源"]) }))).toBe(false);
  });

  it("draws the count boundary where the real data leaves a gap", () => {
    const short = (n: number) => opts(Array.from({ length: n }, () => "短"));
    expect(fitsSegmented(item({ widget: "multiple_button", options: short(SEGMENTED_MAX_OPTIONS) }))).toBe(true);
    expect(fitsSegmented(item({ widget: "multiple_button", options: short(SEGMENTED_MAX_OPTIONS + 1) }))).toBe(false);
  });

  it("draws the width boundary at the pixel budget", () => {
    // 逼近阈值：宽度刚好在预算内/外
    const wide = (n: number) => opts(["宽".repeat(n)]);
    const fitting = [...Array(40).keys()].map((n) => n + 1).filter((n) => estimateSegmentedWidthPx(["宽".repeat(n)]) <= SEGMENTED_MAX_WIDTH_PX);
    const last = Math.max(...fitting);
    expect(fitsSegmented(item({ widget: "multiple_button", options: wide(last) }))).toBe(true);
    expect(fitsSegmented(item({ widget: "multiple_button", options: wide(last + 1) }))).toBe(false);
  });

  it("treats a multiple_button with no options as unrenderable-as-segmented", () => {
    expect(fitsSegmented(item({ widget: "multiple_button", options: [] }))).toBe(false);
    expect(pickControl(item({ widget: "multiple_button" }))).toBe("select");
  });
});
