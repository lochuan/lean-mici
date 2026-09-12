/** API 客户端 + token 管理。
 *
 * 后端契约见 lanlinkd.py 的 ROUTES。要点：
 *  - 认证是 Bearer token（POST /api/login 换取），存 localStorage
 *  - 写成功返回 204 无 body；错误返回 {"error": "..."}
 *  - 401 表示 token 失效（如设备侧改过密码会 revoke_all），要回登录页
 *  - blocked param 返回 403 而非静默跳过（与上游不同，见 FRONTEND_SPEC.md §1）
 */
import type {
  BluetoothStatus, Capabilities, ModelsState, ParamValues, RadarSnapshot, SettingsSchema, StatusSnapshot,
  VehicleState,
} from "./schema";

const TOKEN_KEY = "lanlink_token";

let token = localStorage.getItem(TOKEN_KEY) ?? "";
let onUnauthorized: (() => void) | null = null;

export function getToken(): string {
  return token;
}

export function setToken(t: string): void {
  token = t ?? "";
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const PUBLIC_PATHS = new Set(["/api/login", "/api/setup"]);

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as object) } });

  if (res.status === 401 && !PUBLIC_PATHS.has(path)) {
    // token 失效（含设备侧改密后的全端下线）
    setToken("");
    onUnauthorized?.();
    throw new ApiError("unauthorized", 401);
  }

  if (res.status === 204) return null as T;

  const body = await res.json().catch(() => ({}) as Record<string, unknown>);
  if (!res.ok) {
    const msg = typeof body.error === "string" ? body.error : `HTTP ${res.status}`;
    throw new ApiError(msg, res.status);
  }
  return body as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  // ---- auth ----
  setup: (password: string) => post<null>("/api/setup", { password }),
  login: (password: string) => post<{ token: string }>("/api/login", { password }),
  changePassword: (oldPw: string, newPw: string) =>
    post<null>("/api/password", { old: oldPw, new: newPw }),

  // ---- 批量读（对应上游 getParams / getParamsMetadata）----
  settingsUi: () => request<SettingsSchema>("/api/settings_ui"),
  capabilities: () => request<Capabilities>("/api/capabilities"),
  allParams: () => request<ParamValues>("/api/params/_all"),
  status: () => request<StatusSnapshot>("/api/status"),
  radar: () => request<RadarSnapshot>("/api/radar"),

  // ---- 单 key 写 ----
  putParam: (key: string, value: string) =>
    request<null>(`/api/params/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  getParam: (key: string) => request<{ value: string }>(`/api/params/${encodeURIComponent(key)}`),

  // ---- models ----
  models: () => request<ModelsState>("/api/models"),
  selectModel: (ref: string) => post<null>("/api/models/select", { ref }),
  cancelModel: () => post<null>("/api/models/cancel"),
  refreshModels: () => post<null>("/api/models/refresh"),
  clearModelCache: () => post<null>("/api/models/clear_cache"),
  favModel: (ref: string, on: boolean) => post<null>("/api/models/fav", { ref, on }),

  // ---- vehicle ----
  vehicle: () => request<VehicleState>("/api/vehicle"),
  // 空 name = 清除手动指定，回到自动识别
  selectVehicle: (name: string) => post<null>("/api/vehicle/select", { name }),

  // ---- bluetooth ----
  // 503 时 body 是 params 推导的降级快照，request 会抛 ApiError（message 即 error 字段）
  bluetooth: () => request<BluetoothStatus>("/api/bluetooth"),
  // 操作名与后端 OPERATIONS 对应：power/scan/stop_scan/pair/connect/disconnect/
  // forget/select_audio/test_audio/pairing_response
  bluetoothOp: (operation: string, body?: Record<string, unknown>) =>
    post<{ message?: string; audio_test_delay_ms?: number }>(
      `/api/bluetooth/${encodeURIComponent(operation)}`, body),

  // ---- logs ----
  logs: () => request<unknown>("/api/logs"),
};

/** 登录状态探测：有 token 且能打通一个受保护端点 */
export async function probeSession(): Promise<boolean> {
  if (!token) return false;
  try {
    await api.status();
    return true;
  } catch {
    return false;
  }
}

/** 是否尚未设置密码（首次使用）。
 *
 * 必须用 /api/setup 探测，**不能**用 /api/login 试空密码：登录失败会累计
 * LoginThrottle 计数，5 次即锁 300 秒——用户只要刷新几次登录页就会把自己
 * 锁在门外。setup 在已设密码时返回 409，且不碰防爆破计数。
 */
export async function needsSetup(): Promise<boolean> {
  try {
    // 空密码必然短于 MIN_PASSWORD_LEN，所以未设密码时得到 400 而非真的设上
    await post<null>("/api/setup", {});
    return true;
  } catch (e) {
    if (!(e instanceof ApiError)) return false;
    if (e.status === 409) return false; // 已设过密码
    if (e.status === 400) return true; // 未设密码，被长度校验拦下
    return false;
  }
}
