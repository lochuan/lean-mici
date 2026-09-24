/** 蓝牙面板的纯函数层：设备分组、状态文案、配对提示与音频测试相位机。
 *
 * 从 BluetoothPanel.vue 抽出来是为了可测（仿 radar.ts）——分组规则、
 * 地址大小写比较、音频测试倒计时的相位边界是最容易错的地方。
 * 文案策略与 the_galaxy 的 BluetoothPanel.js 一一对应。
 */
import type { BluetoothDevice, BluetoothPrompt, BluetoothStatus } from "./schema";

/** 蓝牙地址大小写不敏感比较（daemon 侧统一 upper） */
export function sameAddress(a: string | null | undefined, b: string | null | undefined): boolean {
  return String(a ?? "").toUpperCase() === String(b ?? "").toUpperCase();
}

export interface DeviceGroups {
  /** paired/trusted/connected 任一为真 = 我的设备 */
  known: BluetoothDevice[];
  available: BluetoothDevice[];
}

export function groupDevices(devices: BluetoothDevice[]): DeviceGroups {
  const known: BluetoothDevice[] = [];
  const available: BluetoothDevice[] = [];
  for (const d of devices) {
    (d.paired || d.trusted || d.connected ? known : available).push(d);
  }
  return { known, available };
}

export function isPairing(d: BluetoothDevice, status: BluetoothStatus): boolean {
  return !!status.pairing_address && sameAddress(status.pairing_address, d.address);
}

export function isAudioSelected(d: BluetoothDevice, status: BluetoothStatus): boolean {
  return sameAddress(status.selected_audio, d.address);
}

/** 设备副标题的能力标注 */
export function capabilityText(d: BluetoothDevice): string {
  const caps: string[] = [];
  if (d.audio) caps.push("音频");
  if (d.controller) caps.push("控制器");
  return caps.length ? caps.join(" · ") : "蓝牙";
}

/** 列表行的状态文案 */
export function deviceStatusText(d: BluetoothDevice, status: BluetoothStatus): string {
  if (isPairing(d, status)) return "配对中…";
  if (d.connected) return isAudioSelected(d, status) ? "已连接 · 音频输出" : "已连接";
  return d.paired ? "已保存" : "可配对";
}

/** 配对提示卡的说明文案（按 agent 请求类型分叉） */
export function promptDescription(prompt: BluetoothPrompt): string {
  switch (prompt.kind) {
    case "confirmation":
      return "请确认配对请求。";
    case "authorization":
      return "允许该设备连接？";
    case "pin":
      return "请输入设备侧显示的 PIN 码。";
    case "passkey":
      return "请输入设备侧显示的密钥。";
    default:
      return "";
  }
}

/** pin/passkey 需要用户输入数值；其余只需确认 */
export function needsPairValue(prompt: BluetoothPrompt | null): boolean {
  return !!prompt && (prompt.kind === "pin" || prompt.kind === "passkey");
}

/** 音频测试相位机（移植自 mici BluetoothManager.audio_test_phase）。
 *
 * daemon 返回 audio_test_delay_ms 后才设 deadline；之前是 starting。
 * 到点后 "now"（测试音正在播放）维持 3s（daemon 侧 AUDIO_TEST_HOLD_TIME），
 * 再往后 complete。deadline <= 0 与未开始是同一个值——面板用 testActive
 * 区分"没在测"和"请求在路上"。
 */
export type AudioTestPhase = "starting" | "countdown" | "now" | "complete";

export const AUDIO_TEST_HOLD_MS = 3000;

export function audioTestPhase(deadlineMs: number, nowMs: number): { phase: AudioTestPhase; seconds: number } {
  if (deadlineMs <= 0) return { phase: "starting", seconds: 0 };
  const remaining = deadlineMs - nowMs;
  if (remaining > 0) return { phase: "countdown", seconds: Math.max(1, Math.ceil(remaining / 1000)) };
  if (remaining > -AUDIO_TEST_HOLD_MS) return { phase: "now", seconds: 0 };
  return { phase: "complete", seconds: 0 };
}

/** 相位 → 倒计时卡文案 */
export function audioTestLabel(phase: AudioTestPhase, seconds: number): string {
  switch (phase) {
    case "starting":
      return "准备中…";
    case "countdown":
      return String(seconds);
    case "now":
      return "正在播放";
    case "complete":
      return "完成";
  }
}
