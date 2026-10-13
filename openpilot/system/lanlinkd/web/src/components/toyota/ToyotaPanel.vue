<script setup lang="ts">
/** 丰田诊断页：顶部会话条 + 四个标签页。
 *
 * 会话状态每 2 s 轮询 /api/toyota/session（浏览器页面不可见时暂停）。
 * 只有 active 时标签页可用；进入诊断模式会暂停 openpilot，所以先弹确认框。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Loader2, LogOut, Power } from "lucide-vue-next";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { ToyotaSession } from "@/lib/schema";
import { countdownText, errorText, idleRemainingS, sessionView } from "@/lib/toyota";
import Badge from "../ui/Badge.vue";
import Button from "../ui/Button.vue";
import Tabs from "../ui/Tabs.vue";
import BackupsTab from "./BackupsTab.vue";
import ConfirmDialog from "./ConfirmDialog.vue";
import CustomizationTab from "./CustomizationTab.vue";
import DtcTab from "./DtcTab.vue";
import LiveDataTab from "./LiveDataTab.vue";

const TABS = [
  { id: "customization", label: "定制" },
  { id: "dtc", label: "故障码" },
  { id: "live", label: "实时数据" },
  { id: "backups", label: "备份" },
];

const session = ref<ToyotaSession | null>(null);
const tab = ref("customization");
const busy = ref(false);
const confirmOpen = ref(false);
const elapsedS = ref(0);
let fetchedAtMs = Date.now();

const view = computed(() => sessionView(session.value));
const remainingS = computed(() => idleRemainingS(session.value, elapsedS.value));

let pollTimer: ReturnType<typeof setInterval> | undefined;
let clockTimer: ReturnType<typeof setInterval> | undefined;
let polling = false;

/** 每次拿到设备数据就重置本地递减的起点，下次轮询自然校正 */
function setSession(next: ToyotaSession): void {
  session.value = next;
  fetchedAtMs = Date.now();
  elapsedS.value = 0;
}

async function poll(): Promise<void> {
  if (polling || document.visibilityState !== "visible") return;
  polling = true;
  try {
    setSession(await api.toyotaSession());
  } catch (e) {
    // 失败保留上一帧；只在从没拿到过数据时提示
    if (!session.value) toast(errorText(e), "error");
  } finally {
    polling = false;
  }
}

async function enter(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  // 进入过程可能持续十几秒，期间轮询能显示 entering
  session.value = { ...(session.value ?? { idleTimeoutS: 0 }), state: "entering" };
  try {
    setSession(await api.toyotaEnter());
    confirmOpen.value = false;
  } catch (e) {
    confirmOpen.value = false;
    toast(errorText(e), "error");
    await poll();
  } finally {
    busy.value = false;
  }
}

async function exit(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  try {
    await api.toyotaExit();
    toast("已退出诊断模式，openpilot 将恢复", "ok");
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    busy.value = false;
    await poll();
  }
}

onMounted(() => {
  void poll();
  pollTimer = setInterval(() => void poll(), 2000);
  clockTimer = setInterval(() => { elapsedS.value = (Date.now() - fetchedAtMs) / 1000; }, 1000);
});
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
  if (clockTimer) clearInterval(clockTimer);
});
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="sl-card flex flex-wrap items-center gap-3 px-5 py-4">
      <div class="flex min-w-0 flex-1 flex-col gap-1">
        <div class="flex items-center gap-2">
          <span class="text-[13px] text-sl-text-3">诊断模式</span>
          <Badge :kind="view.tone">{{ view.label }}</Badge>
          <Loader2 v-if="session?.state === 'entering'" class="size-4 animate-spin text-sl-text-3" />
        </div>
        <p v-if="view.detail" class="text-[13px] text-sl-danger">{{ view.detail }}</p>
        <p v-if="remainingS !== null" class="sl-tabular text-[12px] text-sl-text-3">
          空闲自动退出倒计时 {{ countdownText(remainingS) }}（任意操作会重新计时）
        </p>
      </div>
      <Button variant="accent" :disabled="!view.canEnter || busy" @click="confirmOpen = true">
        <Power />进入诊断模式
      </Button>
      <Button :disabled="!view.canExit || busy" @click="exit"><LogOut />退出</Button>
    </section>

    <p v-if="!view.tabsEnabled" class="text-[13px] text-sl-text-2">请先进入诊断模式后再使用下面的功能。</p>

    <!-- 未进入诊断模式时整体置灰且不可点 -->
    <div :class="view.tabsEnabled ? '' : 'pointer-events-none select-none opacity-40'" :inert="!view.tabsEnabled">
      <Tabs v-model="tab" :items="TABS">
        <template #customization><CustomizationTab /></template>
        <template #dtc><DtcTab /></template>
        <template #live="{ active }"><LiveDataTab :active="active && view.tabsEnabled" /></template>
        <template #backups="{ active }"><BackupsTab :active="active && view.tabsEnabled" /></template>
      </Tabs>
    </div>

    <ConfirmDialog
      v-model:open="confirmOpen"
      title="进入诊断模式"
      confirm-text="进入"
      :busy="busy"
      @confirm="enter"
    >
      <p>进入后 openpilot 会暂停（pandad 停止，设备按熄火处理），退出或空闲超时后自动恢复。</p>
      <p class="text-sl-warn">请确认车辆已熄火，或挂在 P 挡且完全静止。</p>
    </ConfirmDialog>
  </div>
</template>
