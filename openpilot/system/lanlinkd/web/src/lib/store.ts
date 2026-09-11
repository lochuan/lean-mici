/** 全局状态。
 *
 * 数据流刻意做成"批量拉取 + 版本号驱动刷新"，与上游一致（见 FRONTEND_SPEC.md §1）：
 * 一次 /api/settings_ui + /api/capabilities + /api/params/_all 拉全量，
 * 之后靠 /api/status 里的 paramsVersion 变化来察觉**设备端**的改动
 * （车机屏幕上改了设置，网页要跟着更新）。
 *
 * 写入用乐观更新：先改本地再发请求，失败则回滚并提示。局域网延迟很低，
 * 但 params.put(block=True) 要落盘，乐观更新能让开关不卡手。
 */
import { computed, reactive, readonly } from "vue";
import { api, ApiError } from "./api";
import type {
  Capabilities,
  Item,
  ModelsState,
  Panel,
  ParamValues,
  SettingsSchema,
  StatusSnapshot,
} from "./schema";

type Toast = { id: number; text: string; kind: "ok" | "warn" | "error" };

const state = reactive({
  ready: false,
  loading: false,
  error: "" as string,

  schema: null as SettingsSchema | null,
  caps: {} as Capabilities,
  params: {} as ParamValues,
  status: null as StatusSnapshot | null,
  models: null as ModelsState | null,

  paramsVersion: null as string | null | undefined,
  connected: false,
  pending: new Set<string>(),
  toasts: [] as Toast[],
});

export const store = state;
export const stateRO = readonly(state);

let toastSeq = 0;
export function toast(text: string, kind: Toast["kind"] = "ok"): void {
  const id = ++toastSeq;
  state.toasts.push({ id, text, kind });
  setTimeout(() => {
    const i = state.toasts.findIndex((t) => t.id === id);
    if (i >= 0) state.toasts.splice(i, 1);
  }, kind === "error" ? 6000 : 3000);
}

/** 面板列表，按 schema 的 order 排序；Maps 不在本地 schema 内（按需求排除） */
export const panels = computed<Panel[]>(() => {
  const p = state.schema?.panels ?? [];
  return [...p].sort((a, b) => (a.order ?? 99) - (b.order ?? 99) || a.id.localeCompare(b.id));
});

export function panelById(id: string): Panel | undefined {
  return panels.value.find((p) => p.id === id);
}

/** 全量加载：登录后调用一次 */
export async function loadAll(): Promise<void> {
  state.loading = true;
  state.error = "";
  try {
    const [schema, caps, params] = await Promise.all([
      api.settingsUi(),
      api.capabilities(),
      api.allParams(),
    ]);
    state.schema = schema;
    state.caps = caps;
    state.params = params;
    state.ready = true;
  } catch (e) {
    state.error = e instanceof Error ? e.message : String(e);
    throw e;
  } finally {
    state.loading = false;
  }
}

export async function refreshParams(): Promise<void> {
  state.params = await api.allParams();
}

export async function refreshCapabilities(): Promise<void> {
  state.caps = await api.capabilities();
}

/** 状态轮询：顺带用 paramsVersion 察觉设备端改动 */
export async function pollStatus(): Promise<void> {
  try {
    const s = await api.status();
    state.status = s;
    state.connected = true;

    const seen = state.paramsVersion;
    if (s.paramsVersion !== undefined && s.paramsVersion !== seen) {
      const first = seen === null || seen === undefined;
      state.paramsVersion = s.paramsVersion;
      // 首次只记录基线；之后的变化说明车机端改过设置，要同步过来
      if (!first && state.ready) {
        await Promise.all([refreshParams(), refreshCapabilities()]);
      }
    }
  } catch (e) {
    state.connected = false;
    if (e instanceof ApiError && e.status === 401) throw e;
  }
}

// ---- param 读写 ----

export function rawValue(key: string): string | undefined {
  return state.params[key];
}

export function boolValue(key: string): boolean {
  return state.params[key] === "1";
}

export function numValue(key: string, fallback = 0): number {
  const n = Number(state.params[key]);
  return Number.isFinite(n) ? n : fallback;
}

export function isPending(key: string): boolean {
  return state.pending.has(key);
}

/** 乐观写入；失败回滚并给出设备返回的原因 */
export async function writeParam(key: string, value: string): Promise<boolean> {
  const previous = state.params[key];
  state.params[key] = value;
  state.pending.add(key);
  try {
    await api.putParam(key, value);
    return true;
  } catch (e) {
    // 回滚：设备拒绝了（403 blocked / 400 类型不符 / 404 未知 key）
    if (previous === undefined) delete state.params[key];
    else state.params[key] = previous;

    const msg = e instanceof ApiError ? describeWriteError(e, key) : String(e);
    toast(msg, "error");
    return false;
  } finally {
    state.pending.delete(key);
  }
}

function describeWriteError(e: ApiError, key: string): string {
  switch (e.status) {
    case 403:
      // 我们刻意返回 403 而不是像上游那样静默跳过，所以这里要说清楚
      return `「${key}」被设备保护，不允许远程修改`;
    case 400:
      return `「${key}」的值不合法`;
    case 404:
      return `设备上不存在「${key}」`;
    case 429:
      return "操作过于频繁，请稍后再试";
    default:
      return `写入「${key}」失败：${e.message}`;
  }
}

export async function toggleParam(key: string): Promise<void> {
  await writeParam(key, boolValue(key) ? "0" : "1");
}

// ---- 搜索（对应 sunnylink 的 ⌘K 设置搜索）----

export interface SearchHit {
  panel: Panel;
  item: Item;
  subPanelLabel?: string;
}

export function searchSettings(query: string): SearchHit[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const hits: SearchHit[] = [];
  for (const panel of panels.value) {
    for (const section of panel.sections ?? []) {
      for (const item of section.items ?? []) {
        if (matches(item, q)) hits.push({ panel, item });
      }
      for (const sub of section.sub_panels ?? []) {
        for (const item of sub.items ?? []) {
          if (matches(item, q)) hits.push({ panel, item, subPanelLabel: sub.label });
        }
      }
    }
  }
  return hits.slice(0, 40);
}

function matches(item: Item, q: string): boolean {
  return (
    item.title?.toLowerCase().includes(q) ||
    item.key?.toLowerCase().includes(q) ||
    (item.description?.toLowerCase().includes(q) ?? false)
  );
}

// ---- models ----

export async function loadModels(): Promise<void> {
  state.models = await api.models();
}
