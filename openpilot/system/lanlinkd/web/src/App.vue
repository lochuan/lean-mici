<script setup lang="ts">
/** 根组件：认证 → 加载 → 面板。
 *
 * 路由用 hash（#/steering），沿用旧版前端的做法：设备上是静态文件服务，
 * 没有 history fallback，用 path 路由刷新会 404。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Loader2, LogOut, RefreshCw } from "lucide-vue-next";
import Sidebar from "./components/Sidebar.vue";
import TopBar from "./components/TopBar.vue";
import PanelView from "./components/PanelView.vue";
import HomeView from "./components/HomeView.vue";
import LoginView from "./components/LoginView.vue";
import Toasts from "./components/Toasts.vue";
import { getToken, setToken, setUnauthorizedHandler } from "./lib/api";
import { loadAll, panelById, pollStatus, refreshParams, store, toast } from "./lib/store";

const authed = ref(Boolean(getToken()));
const booting = ref(false);
const current = ref("");
/** <md 的侧边栏 drawer 开关；>=md 常驻，状态无效 */
const sidebarOpen = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;

const panel = computed(() => panelById(current.value));

function readHash(): void {
  // 空 hash 落在首页，与 sunnylink 的 /dashboard 一致
  current.value = (location.hash || "").replace(/^#\/?/, "");
  // 任何 hash 变化（点导航、浏览器后退）都收起手机 drawer
  sidebarOpen.value = false;
}

function navigate(id: string): void {
  location.hash = `#/${id}`;
}

async function boot(): Promise<void> {
  booting.value = true;
  try {
    await loadAll();
    readHash();
    await pollStatus();
    // 4 秒一轮：足够及时，又不会让设备忙于响应（statusd 本身 2Hz 更新）
    timer = setInterval(() => void pollStatus(), 4000);
  } catch {
    // loadAll 已把错误写进 store.error，这里交给模板展示
  } finally {
    booting.value = false;
  }
}

function logout(): void {
  setToken("");
  authed.value = false;
  if (timer) clearInterval(timer);
}

async function manualRefresh(): Promise<void> {
  try {
    await refreshParams();
    toast("已刷新", "ok");
  } catch {
    toast("刷新失败", "error");
  }
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape") sidebarOpen.value = false;
}

onMounted(() => {
  // token 失效（含设备端改密后的全端下线）→ 回登录页
  setUnauthorizedHandler(() => {
    authed.value = false;
    if (timer) clearInterval(timer);
    toast("登录已失效，请重新登录", "warn");
  });
  window.addEventListener("hashchange", readHash);
  window.addEventListener("keydown", onKeydown);
  if (authed.value) void boot();
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
  window.removeEventListener("hashchange", readHash);
  window.removeEventListener("keydown", onKeydown);
});

async function onAuthed(): Promise<void> {
  authed.value = true;
  await boot();
}
</script>

<template>
  <LoginView v-if="!authed" @authed="onAuthed" />

  <div v-else class="flex h-dvh overflow-hidden bg-sl-bg">
    <!-- 手机端 drawer 遮罩；md+ 侧边栏常驻，遮罩不渲染 -->
    <Transition
      enter-active-class="transition-opacity duration-200"
      leave-active-class="transition-opacity duration-200"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="sidebarOpen"
        class="fixed inset-0 z-30 bg-black/50 md:hidden"
        aria-hidden="true"
        @click="sidebarOpen = false"
      />
    </Transition>

    <Sidebar :current="current" :open="sidebarOpen" @navigate="navigate">
      <template #footer>
        <div class="flex items-center gap-1 border-t border-sl-border p-3">
          <button
            type="button"
            class="flex h-9 flex-1 items-center gap-2 rounded-lg px-3 text-[13px] text-sl-text-2 transition-colors hover:bg-sl-surface-2 hover:text-sl-text-1"
            @click="manualRefresh"
          >
            <RefreshCw class="size-3.5" />
            刷新
          </button>
          <button
            type="button"
            class="grid size-9 place-items-center rounded-lg text-sl-text-3 transition-colors hover:bg-sl-surface-2 hover:text-sl-danger"
            title="退出登录"
            @click="logout"
          >
            <LogOut class="size-4" />
          </button>
        </div>
      </template>
    </Sidebar>

    <main class="flex min-w-0 flex-1 flex-col">
      <TopBar @menu="sidebarOpen = true" />

      <div class="flex-1 overflow-y-auto">
        <div v-if="booting" class="grid h-full place-items-center">
          <Loader2 class="size-6 animate-spin text-sl-text-3" />
        </div>

        <div v-else-if="store.error" class="mx-auto max-w-[560px] px-6 py-16 text-center">
          <p class="text-sm text-sl-danger">加载失败：{{ store.error }}</p>
          <button
            type="button"
            class="mt-4 text-[13px] text-sl-accent underline underline-offset-4"
            @click="boot"
          >
            重试
          </button>
        </div>

        <PanelView v-else-if="panel" :panel="panel" />
        <HomeView v-else @navigate="navigate" />
      </div>
    </main>
  </div>

  <Toasts />
</template>
