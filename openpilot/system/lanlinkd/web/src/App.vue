<script setup lang="ts">
/** 根组件：加载 → 面板。局域网无认证，直接进主界面。
 *
 * 路由用 hash（#/steering），沿用旧版前端的做法：设备上是静态文件服务，
 * 没有 history fallback，用 path 路由刷新会 404。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { DialogContent, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from "reka-ui";
import { Loader2 } from "lucide-vue-next";
import Sidebar from "./components/Sidebar.vue";
import TopBar from "./components/TopBar.vue";
import PanelView from "./components/PanelView.vue";
import HomeView from "./components/HomeView.vue";
import Toasts from "./components/Toasts.vue";
import { loadAll, panelById, pollStatus, refreshParams, store, toast } from "./lib/store";

const booting = ref(false);
const current = ref("");
/** <md 的导航 drawer 开关；>=md 侧边栏常驻，drawer 不渲染 */
const sidebarOpen = ref(false);
/** 本次开/关由键盘触发：不播放滑入滑出，键盘操作要即时 */
const drawerInstant = ref(false);
let lastInputKeyboard = false;
const topBar = ref<InstanceType<typeof TopBar> | null>(null);
const desktop = window.matchMedia("(min-width: 768px)");
let timer: ReturnType<typeof setInterval> | undefined;

const panel = computed(() => panelById(current.value));

function setSidebar(open: boolean): void {
  if (open === sidebarOpen.value) return;
  drawerInstant.value = lastInputKeyboard;
  sidebarOpen.value = open;
}

function readHash(): void {
  // 空 hash 落在首页，与 sunnylink 的 /dashboard 一致
  current.value = (location.hash || "").replace(/^#\/?/, "");
  // 任何 hash 变化（点导航、浏览器后退）都收起手机 drawer
  setSidebar(false);
}

function navigate(id: string): void {
  location.hash = `#/${id}`;
  setSidebar(false);
}

/** drawer 关闭后焦点回到汉堡按钮（手机浏览器点按不一定给按钮焦点，不能依赖 reka 记录的触发元素） */
function restoreMenuFocus(e: Event): void {
  e.preventDefault();
  topBar.value?.focusMenu();
}

/** 跨到 >=md：drawer 立即卸载，reka 的 aria-hidden / pointer-events 锁随之解除 */
function onBreakpoint(e: MediaQueryListEvent): void {
  if (!e.matches) return;
  drawerInstant.value = true;
  sidebarOpen.value = false;
}

function onKeydownCapture(): void {
  lastInputKeyboard = true;
}
function onPointerdownCapture(): void {
  lastInputKeyboard = false;
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

async function manualRefresh(): Promise<void> {
  try {
    await refreshParams();
    toast("已刷新", "ok");
  } catch {
    toast("刷新失败", "error");
  }
}

onMounted(() => {
  window.addEventListener("hashchange", readHash);
  window.addEventListener("keydown", onKeydownCapture, true);
  window.addEventListener("pointerdown", onPointerdownCapture, true);
  desktop.addEventListener("change", onBreakpoint);
  void boot();
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
  window.removeEventListener("hashchange", readHash);
  window.removeEventListener("keydown", onKeydownCapture, true);
  window.removeEventListener("pointerdown", onPointerdownCapture, true);
  desktop.removeEventListener("change", onBreakpoint);
});
</script>

<template>
  <div class="flex h-dvh overflow-hidden bg-sl-bg">
    <!-- >=md 常驻侧边栏 -->
    <aside class="hidden w-[248px] shrink-0 border-r border-sl-border bg-sl-surface md:block">
      <Sidebar :current="current" @navigate="navigate" @refresh="manualRefresh" />
    </aside>

    <!-- <md 导航 drawer：reka Dialog 负责焦点受限、Escape / 遮罩关闭；关闭即卸载 -->
    <DialogRoot :open="sidebarOpen" @update:open="setSidebar">
      <DialogPortal>
        <DialogOverlay
          :class="[
            'fixed inset-0 z-40 bg-black/50 duration-200 md:hidden',
            !drawerInstant &&
              'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0',
          ]"
        />
        <DialogContent
          :class="[
            'fixed inset-y-0 left-0 z-40 w-[248px] max-w-[85vw] border-r border-sl-border bg-sl-surface duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] focus:outline-none md:hidden',
            !drawerInstant &&
              'data-[state=open]:animate-in data-[state=open]:slide-in-from-left data-[state=closed]:animate-out data-[state=closed]:slide-out-to-left',
          ]"
          aria-describedby=""
          @close-auto-focus="restoreMenuFocus"
        >
          <DialogTitle class="sr-only">导航菜单</DialogTitle>
          <Sidebar :current="current" @navigate="navigate" @refresh="manualRefresh" />
        </DialogContent>
      </DialogPortal>
    </DialogRoot>

    <main class="flex min-w-0 flex-1 flex-col">
      <TopBar ref="topBar" :menu-open="sidebarOpen" @menu="setSidebar(true)" />

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
