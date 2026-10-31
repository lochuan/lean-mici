<script setup lang="ts">
/** 诊断会话条：未进入 / 进入中 / 已进入（空闲倒计时） / 退出中。
 *
 * 进入诊断模式会暂停 openpilot，所以先弹确认框。
 * 未进入时点功能项会递增 shakeTick，这里据此重播抖动动画。
 */
import { computed, ref, watch } from "vue";
import { Loader2, LogOut, Power } from "lucide-vue-next";
import { countdownText } from "@/lib/toyota";
import { enter, exit, remainingS, sessionBusy, sessionNow, shakeTick, currentSessionView } from "@/lib/toyotaSession";
import { cn } from "@/lib/utils";
import Button from "../ui/Button.vue";
import ConfirmDialog from "./ConfirmDialog.vue";

const confirmOpen = ref(false);
const shaking = ref(false);

const entering = computed(() => sessionNow.value?.state === "entering");
const exiting = computed(() => sessionBusy.value && sessionNow.value?.state === "active");
const active = computed(() => sessionNow.value?.state === "active" && !exiting.value);

const title = computed(() => {
  if (entering.value) return "正在进入…";
  if (exiting.value) return "正在退出…";
  return active.value ? "已进入诊断模式" : "未进入诊断模式";
});
const subtitle = computed(() => {
  if (entering.value) return "正在停止 pandad、建立诊断会话";
  if (exiting.value) return "正在恢复 pandad";
  if (active.value && remainingS.value !== null) return `空闲 ${countdownText(remainingS.value)} 后自动退出 · pandad 已停止`;
  return currentSessionView.value.detail || "pandad 正常运行，车辆功能不受影响";
});

watch(shakeTick, () => {
  shaking.value = false;
  // 下一帧再置回，同一个 class 才会重新播放动画
  requestAnimationFrame(() => { shaking.value = true; });
});

async function confirmEnter(): Promise<void> {
  await enter();
  confirmOpen.value = false;
}
</script>

<template>
  <section
    :class="cn('sl-card flex items-center gap-3 px-4 py-3', shaking && 'animate-sl-shake')"
    @animationend="shaking = false"
  >
    <span
      :class="cn('size-2.5 shrink-0 rounded-full', active ? 'bg-sl-accent shadow-[0_0_10px] shadow-sl-accent' : entering || exiting ? 'animate-pulse bg-sl-warn' : 'bg-sl-text-3')"
    />
    <div class="flex min-w-0 flex-1 flex-col">
      <span class="text-[15px] font-semibold text-sl-text-1">{{ title }}</span>
      <span :class="cn('truncate text-[12px]', currentSessionView.detail && !active ? 'text-sl-danger' : 'sl-tabular text-sl-text-3')">{{ subtitle }}</span>
    </div>
    <Loader2 v-if="entering || exiting" class="size-4 shrink-0 animate-spin text-sl-text-3" />
    <Button v-else-if="active" variant="danger" :disabled="!currentSessionView.canExit || sessionBusy" @click="exit">
      <LogOut />退出
    </Button>
    <Button v-else variant="accent" :disabled="!currentSessionView.canEnter || sessionBusy" @click="confirmOpen = true">
      <Power />进入
    </Button>

    <ConfirmDialog v-model:open="confirmOpen" title="进入诊断模式" confirm-text="进入" :busy="sessionBusy" @confirm="confirmEnter">
      <p>进入后 openpilot 会暂停（pandad 停止，设备按熄火处理），退出或空闲超时后自动恢复。</p>
      <p class="text-sl-warn">请确认车辆已熄火，或挂在 P 挡且完全静止。</p>
    </ConfirmDialog>
  </section>
</template>
