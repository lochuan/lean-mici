/** TechStream 诊断会话的模块级状态：轮询、空闲倒计时、进入 / 退出。
 *
 * 会话状态每 2 s 轮询 /api/toyota/session（浏览器页面不可见时暂停）。
 * 状态放在模块里而不是组件里：页面栈切换时各页面都要读它，会话条也要读它。
 */
import { computed, reactive, ref } from "vue";
import { api } from "./api";
import { toast } from "./store";
import type { ToyotaSession } from "./schema";
import { errorText, idleRemainingS, sessionView } from "./toyota";

const state = reactive({
  session: null as ToyotaSession | null,
  /** 进入 / 退出请求进行中 */
  busy: false,
  /** 拿到设备数据之后本地过去的秒数，用来递减空闲倒计时 */
  elapsedS: 0,
});

/** 未进入时点功能项会递增它，会话条据此重播抖动动画 */
export const shakeTick = ref(0);

export const sessionNow = computed(() => state.session);
export const sessionBusy = computed(() => state.busy);
export const currentSessionView = computed(() => sessionView(state.session));
export const remainingS = computed(() => idleRemainingS(state.session, state.elapsedS));

let fetchedAtMs = Date.now();
let polling = false;
let selfExiting = false;

/** 每次拿到设备数据就重置本地递减的起点，下次轮询自然校正 */
function setSession(next: ToyotaSession): void {
  const wasActive = state.session?.state === "active";
  state.session = next;
  fetchedAtMs = Date.now();
  state.elapsedS = 0;
  if (wasActive && next.state === "idle" && !selfExiting) toast("空闲超时，已自动退出诊断模式", "warn");
}

/** 任何操作都重置空闲计时（后端在每次诊断请求时同样刷新）；下次轮询再校正 */
export function touch(): void {
  if (state.session?.state !== "active") return;
  state.session = { ...state.session, idleRemainingS: state.session.idleTimeoutS };
  fetchedAtMs = Date.now();
  state.elapsedS = 0;
}

export async function poll(): Promise<void> {
  if (polling || document.visibilityState !== "visible") return;
  polling = true;
  try {
    setSession(await api.toyotaSession());
  } catch (e) {
    // 失败保留上一帧；只在从没拿到过数据时提示
    if (!state.session) toast(errorText(e), "error");
  } finally {
    polling = false;
  }
}

export async function enter(): Promise<boolean> {
  if (state.busy) return false;
  state.busy = true;
  // 进入过程可能持续十几秒，期间轮询能显示 entering
  state.session = { ...(state.session ?? { idleTimeoutS: 0 }), state: "entering" };
  try {
    setSession(await api.toyotaEnter());
    return true;
  } catch (e) {
    toast(errorText(e), "error");
    await poll();
    return false;
  } finally {
    state.busy = false;
  }
}

export async function exit(): Promise<void> {
  if (state.busy) return;
  state.busy = true;
  selfExiting = true;
  try {
    await api.toyotaExit();
    setSession(await api.toyotaSession());
    toast("已退出诊断模式，openpilot 将恢复", "ok");
  } catch (e) {
    toast(errorText(e), "error");
    await poll();
  } finally {
    selfExiting = false;
    state.busy = false;
  }
}

/** 功能入口 / 写入前的门：未进入诊断模式就抖动会话条并提示 */
export function requireActive(): boolean {
  if (state.session?.state === "active") return true;
  toast("请先进入诊断模式", "warn");
  shakeTick.value++;
  return false;
}

let pollTimer: ReturnType<typeof setInterval> | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;
let watchers = 0;

/** 面板挂载时调用，返回停止函数；多处同时挂载只起一套定时器 */
export function startSessionWatch(): () => void {
  if (watchers++ === 0) {
    void poll();
    pollTimer = setInterval(() => void poll(), 2000);
    clockTimer = setInterval(() => { state.elapsedS = (Date.now() - fetchedAtMs) / 1000; }, 1000);
  }
  return () => {
    if (--watchers > 0) return;
    clearInterval(pollTimer);
    clearInterval(clockTimer);
  };
}
