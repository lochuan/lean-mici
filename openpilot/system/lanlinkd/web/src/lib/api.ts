/** API 客户端。
 *
 * 后端契约见 lanlinkd.py 的 ROUTES。要点：
 *  - 无认证：局域网单用户直接访问（无 Authorization 头、无登录页）
 *  - 写成功返回 204 无 body；错误返回 {"error": "..."}
 *  - blocked param 返回 403 而非静默跳过（与上游不同，见 FRONTEND_SPEC.md §1）
 */
import type {
  AvoidanceSnapshot, BluetoothStatus, CalibrationStatus, Capabilities, ModelsState, ParamValues, SettingsSchema,
  SoftwareStatus, StatusSnapshot, VehicleState, WifiStatus,
} from "./schema";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };

  const res = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as object) } });

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
  // ---- 批量读（对应上游 getParams / getParamsMetadata）----
  settingsUi: () => request<SettingsSchema>("/api/settings_ui"),
  capabilities: () => request<Capabilities>("/api/capabilities"),
  allParams: () => request<ParamValues>("/api/params/_all"),
  status: () => request<StatusSnapshot>("/api/status"),
  avoidance: () => request<AvoidanceSnapshot>("/api/avoidance"),
  calibrationStatus: () => request<CalibrationStatus>("/api/calibration/status"),
  calibrationStart: () => request<CalibrationStatus>("/api/calibration/start", { method: "POST" }),
  calibrationStop: () => request<CalibrationStatus>("/api/calibration/stop", { method: "POST" }),

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

  // ---- wifi ----
  wifi: () => request<WifiStatus>("/api/wifi"),
  wifiOp: (operation: string, body: Record<string, unknown>) =>
    post<Record<string, unknown>>(`/api/wifi/${encodeURIComponent(operation)}`, body),

  // ---- software (updater) ----
  software: () => request<SoftwareStatus>("/api/software"),
  softwareOp: (action: string) =>
    post<null>(`/api/software/${encodeURIComponent(action)}`),

  // ---- logs ----
  logs: () => request<unknown>("/api/logs"),
};
