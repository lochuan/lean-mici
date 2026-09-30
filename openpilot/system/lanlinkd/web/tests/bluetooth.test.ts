import { describe, expect, it } from "vitest";
import type { BluetoothDevice, BluetoothStatus } from "../src/lib/schema";
import {
  AUDIO_TEST_HOLD_MS,
  audioTestLabel,
  audioTestPhase,
  capabilityText,
  deviceStatusText,
  groupDevices,
  isPairing,
  needsPairValue,
  promptDescription,
  sameAddress,
} from "../src/lib/bluetooth";

const dev = (address: string, over: Partial<BluetoothDevice> = {}): BluetoothDevice => ({
  address,
  name: `Device ${address}`,
  paired: false,
  trusted: false,
  connected: false,
  blocked: false,
  rssi: null,
  uuids: [],
  audio: false,
  controller: false,
  ...over,
});

const st = (over: Partial<BluetoothStatus> = {}): BluetoothStatus => ({
  available: true,
  enabled: true,
  powered: true,
  discovering: false,
  offroad: true,
  selected_audio: "",
  pairing_address: "",
  devices: [],
  prompt: null,
  error: "",
  ...over,
});

describe("groupDevices", () => {
  it("splits by paired/trusted/connected, keeping order", () => {
    const devices = [
      dev("AA", { paired: true }),
      dev("BB"),
      dev("CC", { trusted: true }),
      dev("DD", { connected: true }),
      dev("EE", { audio: true }),
    ];
    const { known, available } = groupDevices(devices);
    expect(known.map((d) => d.address)).toEqual(["AA", "CC", "DD"]);
    expect(available.map((d) => d.address)).toEqual(["BB", "EE"]);
  });

  it("keeps blocked-but-known devices in known", () => {
    // blocked 只影响扫描期是否自动展示，已配对设备仍属于"我的设备"
    const { known } = groupDevices([dev("AA", { paired: true, blocked: true })]);
    expect(known).toHaveLength(1);
  });
});

describe("sameAddress", () => {
  it("is case-insensitive and null-safe", () => {
    expect(sameAddress("aa:bb", "AA:BB")).toBe(true);
    expect(sameAddress("", null)).toBe(true);
    expect(sameAddress("aa:bb", "aa:bc")).toBe(false);
    expect(sameAddress(undefined, "")).toBe(true);
  });
});

describe("deviceStatusText", () => {
  it("prioritizes pairing over everything", () => {
    const status = st({ pairing_address: "aa:bb" });
    expect(deviceStatusText(dev("AA:BB", { connected: true }), status)).toBe("配对中…");
  });

  it("marks the selected audio output", () => {
    const status = st({ selected_audio: "AA:BB" });
    expect(deviceStatusText(dev("AA:BB", { connected: true }), status)).toBe("已连接 · 音频输出");
    expect(deviceStatusText(dev("CC:DD", { connected: true }), status)).toBe("已连接");
  });

  it("falls back to saved / ready-to-pair", () => {
    expect(deviceStatusText(dev("AA", { paired: true }), st())).toBe("已保存");
    expect(deviceStatusText(dev("AA"), st())).toBe("可配对");
  });
});

describe("isPairing", () => {
  it("matches case-insensitively and requires a pairing address", () => {
    expect(isPairing(dev("AA:BB"), st({ pairing_address: "aa:bb" }))).toBe(true);
    expect(isPairing(dev("AA:BB"), st({ pairing_address: "" }))).toBe(false);
  });
});

describe("capabilityText", () => {
  it("labels audio/controller combinations", () => {
    expect(capabilityText(dev("A", { audio: true }))).toBe("音频");
    expect(capabilityText(dev("A", { controller: true }))).toBe("控制器");
    expect(capabilityText(dev("A", { audio: true, controller: true }))).toBe("音频 · 控制器");
    expect(capabilityText(dev("A"))).toBe("蓝牙");
  });
});

describe("prompt helpers", () => {
  it("describes each agent request kind", () => {
    expect(promptDescription({ id: "1", kind: "confirmation" })).toContain("确认");
    expect(promptDescription({ id: "1", kind: "authorization" })).toContain("允许");
    expect(promptDescription({ id: "1", kind: "pin" })).toContain("PIN");
    expect(promptDescription({ id: "1", kind: "passkey" })).toContain("密钥");
    expect(promptDescription({ id: "1", kind: "whatever" })).toBe("");
  });

  it("only pin/passkey need a value", () => {
    expect(needsPairValue({ id: "1", kind: "pin" })).toBe(true);
    expect(needsPairValue({ id: "1", kind: "passkey" })).toBe(true);
    expect(needsPairValue({ id: "1", kind: "confirmation" })).toBe(false);
    expect(needsPairValue(null)).toBe(false);
  });
});

describe("audioTestPhase", () => {
  const deadline = 10_000;

  it("is starting before the daemon replies (deadline 0)", () => {
    expect(audioTestPhase(0, 5_000).phase).toBe("starting");
    expect(audioTestPhase(-1, 5_000).phase).toBe("starting");
  });

  it("counts down whole seconds, never below 1", () => {
    expect(audioTestPhase(deadline, 9_999)).toEqual({ phase: "countdown", seconds: 1 });
    expect(audioTestPhase(deadline, 8_500)).toEqual({ phase: "countdown", seconds: 2 });
    expect(audioTestPhase(deadline, 0)).toEqual({ phase: "countdown", seconds: 10 });
  });

  it("holds 'now' for the 3s play window, then completes", () => {
    expect(audioTestPhase(deadline, deadline).phase).toBe("now");
    expect(audioTestPhase(deadline, deadline + AUDIO_TEST_HOLD_MS - 1).phase).toBe("now");
    expect(audioTestPhase(deadline, deadline + AUDIO_TEST_HOLD_MS).phase).toBe("complete");
  });
});

describe("audioTestLabel", () => {
  it("maps phases to labels", () => {
    expect(audioTestLabel("starting", 0)).toBe("准备中…");
    expect(audioTestLabel("countdown", 3)).toBe("3");
    expect(audioTestLabel("now", 0)).toBe("正在播放");
    expect(audioTestLabel("complete", 0)).toBe("完成");
  });
});
