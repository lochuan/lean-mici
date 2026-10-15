import { describe, expect, it } from "vitest";
import { devicePill, networkLabel } from "../src/lib/deviceState";

describe("devicePill", () => {
  it("shows Connecting... when the poll fails or data is stale", () => {
    // sunnylink 词汇：拿不到数据一律 Connecting...（实测设备离线时的显示）
    expect(devicePill({ connected: false, alwaysOffroad: false })).toEqual({
      text: "Connecting...",
      tone: "danger",
    });
    expect(devicePill({ connected: true, stale: true, alwaysOffroad: false })).toEqual({
      text: "Connecting...",
      tone: "warn",
    });
  });

  it("shows Offroad when online and not started", () => {
    // 点火但未过 startup_conditions（过热/启动中）也是 Offroad：started 才是结论
    expect(devicePill({ connected: true, alwaysOffroad: false, started: false })).toEqual({
      text: "Offroad",
      tone: "muted",
    });
  });

  it("shows Onroad only from deviceState.started", () => {
    expect(devicePill({ connected: true, alwaysOffroad: false, started: true })).toEqual({
      text: "Onroad",
      tone: "accent",
    });
  });

  it("always-offroad beats started (user-forced state wins)", () => {
    expect(devicePill({ connected: true, alwaysOffroad: true, started: true })).toEqual({
      text: "Always Offroad",
      tone: "warn",
    });
  });

  it("connection failure wins over everything (danger tone is the signal)", () => {
    expect(devicePill({ connected: false, stale: true, alwaysOffroad: true, started: true }).tone)
      .toBe("danger");
  });
});

describe("networkLabel", () => {
  it("maps cereal NetworkType enum values", () => {
    expect(networkLabel(1)).toBe("WiFi");
    expect(networkLabel(4)).toBe("Cellular 4G");
    expect(networkLabel(6)).toBe("Ethernet");
  });

  it("renders unknown/absent as a dash", () => {
    expect(networkLabel(0)).toBe("—");
    expect(networkLabel(undefined)).toBe("—");
  });
});
