/** 顶栏设备状态胶囊的纯逻辑。
 *
 * 状态词刻意对齐 sunnylink /dashboard 右上角的 "Device status" 胶囊
 * （实测 2026-09-12：设备离线显示 "Connecting..."，在线但熄火显示
 * "Offroad"）——保持英文原词不翻译，与上游一一对应便于对照。
 *
 * Onroad 判定用 deviceState.started（hardwared 的 onroad 汇总结论），
 * 不用点火：点火但未过 startup_conditions（过热/未启动完）也是 Offroad。
 */
export interface DevicePill {
  text: string;
  tone: "accent" | "warn" | "danger" | "muted";
}

export interface DevicePillInput {
  /** /api/status 最近一次轮询是否成功 */
  connected: boolean;
  /** statusd 快照过期（SubMaster 停更：manager 重启中等） */
  stale?: boolean;
  /** params.OffroadMode === "1"，用户强制驻车（CLEAR_ON_MANAGER_START） */
  alwaysOffroad: boolean;
  /** deviceState.started，manager 判定 onroad */
  started?: boolean;
}

export function devicePill(input: DevicePillInput): DevicePill {
  if (!input.connected) return { text: "Connecting...", tone: "danger" };
  if (input.stale) return { text: "Connecting...", tone: "warn" };
  if (input.alwaysOffroad) return { text: "Always Offroad", tone: "warn" };
  if (input.started) return { text: "Onroad", tone: "accent" };
  return { text: "Offroad", tone: "muted" };
}

/** cereal NetworkType 枚举 → 弹层显示词（log.capnp：0 none…6 ethernet） */
export function networkLabel(t: number | undefined): string {
  switch (t) {
    case 1: return "WiFi";
    case 2: return "Cellular 2G";
    case 3: return "Cellular 3G";
    case 4: return "Cellular 4G";
    case 5: return "Cellular 5G";
    case 6: return "Ethernet";
    default: return "—";
  }
}
