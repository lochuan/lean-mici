<script setup lang="ts">
/** 诊断页：相机安装 / 跑偏 / 画龙 / 转向能力。
 *
 * 页面级 2s 轮询 /api/diagnostics（不并入 /api/status），失败保留上一帧。
 * 行驶统计只算本次行驶，停车后保留到下次启动。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { DiagnosticsReport } from "@/lib/schema";
import { driveText } from "@/lib/diagnostics";
import CameraCard from "./CameraCard.vue";
import DriftCard from "./DriftCard.vue";
import SteeringCard from "./SteeringCard.vue";
import WeaveCard from "./WeaveCard.vue";

const report = ref<DiagnosticsReport | null>(null);
const failed = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    report.value = await api.diagnostics();
    failed.value = false;
  } catch {
    // 失败保留上一帧，只在从没拿到过数据时提示
    failed.value = true;
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
    <div v-if="report" class="flex items-center gap-2 text-[13px] text-sl-text-2">
      <span class="inline-block size-2 rounded-full" :class="driving ? 'animate-pulse bg-sl-accent' : 'bg-sl-text-3'" />
      {{ driveText(report.drive) }}
    </div>

    <template v-if="report">
      <CameraCard :camera="report.camera" />
      <DriftCard :drift="report.drift" />
      <WeaveCard :weave="report.weave" :driving="driving" />
      <SteeringCard :steering="report.steering" />
    </template>
    <div v-else class="sl-card px-5 py-6 text-center text-[13px] text-sl-text-3">
      {{ failed ? "无法读取诊断数据，请确认设备在线" : "正在读取诊断数据…" }}
    </div>
  </div>
</template>
