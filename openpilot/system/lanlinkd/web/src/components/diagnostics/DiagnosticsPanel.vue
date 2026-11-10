<script setup lang="ts">
/** 诊断页：相机安装 / 跑偏 / 画龙 / 转向能力 / 盲区监测。
 *
 * 页面级 2s 轮询 /api/diagnostics（不并入 /api/status），失败保留上一帧。
 * 行驶统计只算本次行驶，停车后保留到下次启动。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Loader2 } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { DiagnosticsReport } from "@/lib/schema";
import { driveText } from "@/lib/diagnostics";
import Button from "../ui/Button.vue";
import BsmCard from "./BsmCard.vue";
import CameraCard from "./CameraCard.vue";
import DriftCard from "./DriftCard.vue";
import SteeringCard from "./SteeringCard.vue";
import WeaveCard from "./WeaveCard.vue";

const report = ref<DiagnosticsReport | null>(null);
const failed = ref(false);
const loading = ref(true);
const refreshing = ref(false);
const updatedAt = ref("");
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    report.value = await api.diagnostics();
    failed.value = false;
    updatedAt.value = new Date().toLocaleTimeString();
  } catch {
    // 保留上一帧，并明确标注更新失败。
    failed.value = true;
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), 2000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

const driving = computed(() => report.value?.drive.started ?? false);
</script>

<template>
  <div class="flex flex-col gap-4">
    <div v-if="failed" role="status" class="flex flex-wrap items-center gap-3 rounded-lg bg-sl-danger/10 px-4 py-3 ring-1 ring-inset ring-sl-danger/25">
      <div class="min-w-0 flex-1 text-[13px]">
        <p class="text-sl-danger">{{ report ? "诊断数据更新失败，请确认设备在线" : "无法读取诊断数据，请确认设备在线" }}</p>
        <p v-if="updatedAt" class="mt-1 text-sl-text-2">显示上次数据，可能已过期 · 上次更新 {{ updatedAt }}</p>
      </div>
      <Button size="sm" :disabled="refreshing" @click="poll">
        <Loader2 v-if="refreshing" class="animate-spin" />重试
      </Button>
    </div>
    <p v-else-if="updatedAt" class="text-[12px] text-sl-text-2">上次更新 {{ updatedAt }}</p>
    <div v-if="report" class="flex items-center gap-2 text-[13px] text-sl-text-2">
      <span class="inline-block size-2 rounded-full" :class="driving && !failed ? 'animate-pulse bg-sl-accent' : 'bg-sl-text-3'" />
      {{ failed ? "上次状态：" : "" }}{{ driveText(report.drive) }}
    </div>

    <template v-if="report">
      <CameraCard :camera="report.camera" />
      <DriftCard :drift="report.drift" />
      <WeaveCard :weave="report.weave" :driving="driving" />
      <SteeringCard :steering="report.steering" />
      <BsmCard :bsm="report.bsm" :driving="driving && !failed" />
    </template>
    <div v-else-if="loading" class="sl-card flex items-center justify-center gap-2 px-5 py-6 text-[13px] text-sl-text-2">
      <Loader2 class="size-4 animate-spin" />正在读取诊断数据…
    </div>
  </div>
</template>
