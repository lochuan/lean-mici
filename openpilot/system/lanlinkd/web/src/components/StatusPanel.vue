<script setup lang="ts">
/** 状态页：设备遥测（CPU/GPU/内存/温度/功耗）+ 模型来源（最近 50 帧大/小模型）。
 *
 * 遥测用页面级 1s 轮询而不是全局 4s 的 pollStatus——那个还承担着
 * paramsVersion 同步，不该被高频触发；这里只读不写 store。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import { replyTimelinessText } from "@/lib/modelStatus";
import type { DeviceStatus, ModelStatus } from "@/lib/schema";
import Badge from "./ui/Badge.vue";

const dev = ref<DeviceStatus | null>(null);
const model = ref<ModelStatus | null>(null);
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    const s = await api.status();
    dev.value = s.device ?? null;
    model.value = s.model ?? null;
  } catch {
    // 失败保留上一帧，指标显示为旧值总比闪烁好
  }
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), 1000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

// ---- 派生指标（null → 显示 "—"）----

const cpuTemp = computed(() => maxOf(dev.value?.cpuTempC));
const gpuTemp = computed(() => maxOf(dev.value?.gpuTempC));
const cpuCores = computed(() => dev.value?.cpuUsagePercent ?? []);
const memFree = computed(() => {
  const u = dev.value?.memoryUsagePercent;
  return typeof u === "number" ? 100 - u : null;
});

function maxOf(list: number[] | undefined): number | null {
  const l = list ?? [];
  return l.length ? Math.max(...l) : null;
}

function fmt(v: number | null | undefined, unit = "", digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}${unit ? ` ${unit}` : ""}`;
}

const fields = computed(() => [
  { label: "CPU 温度", value: fmt(cpuTemp.value, "°C") },
  { label: "GPU 温度", value: fmt(gpuTemp.value, "°C") },
  { label: "内存温度", value: fmt(dev.value?.memoryTempC, "°C") },
  { label: "内存剩余", value: fmt(memFree.value, "%") },
  { label: "存储剩余", value: fmt(dev.value?.freeSpacePercent, "%") },
  { label: "GPU 负载", value: fmt(dev.value?.gpuUsagePercent, "%") },
  { label: "整机功耗", value: fmt(dev.value?.powerDrawW, "W", 1) },
  { label: "风扇", value: fmt(dev.value?.fanSpeedPercentDesired, "%") },
]);

/** thermalStatus 枚举（cereal DeviceState）：0 ok / 1 warm(弃用) / 2 overheated / 3 critical */
const thermal = computed(() => {
  switch (dev.value?.thermalStatus) {
    case 2:
      return { text: "过热降频", kind: "danger" as const };
    case 3:
      return { text: "严重过热", kind: "danger" as const };
    case 1:
      return { text: "偏热", kind: "warn" as const };
    default:
      return { text: "温度正常", kind: "accent" as const };
  }
});

const modelFrames = computed(() => model.value?.frames ?? []);
const bigCount = computed(() => modelFrames.value.filter((f) => f === 1).length);

const LINK_TEXT: Record<string, string> = {
  connecting: "连接中",
  connected: "已连接",
  blip: "瞬断",
  restart: "服务端重启",
  lost: "已断开",
};

const modelFields = computed(() => {
  const m = model.value;
  const s = m?.serverMs;
  const avgMax = (a?: number | null, b?: number | null) =>
    a == null ? "—" : `${a.toFixed(1)} / ${b == null ? "—" : b.toFixed(1)} ms`;
  return [
    { label: "链路状态", value: LINK_TEXT[m?.linkState ?? ""] ?? (m?.linkState || "—") },
    { label: "大模型往返 均/峰", value: avgMax(m?.bigLatencyAvgMs, m?.bigLatencyMaxMs) },
    { label: "REPLY 按时", value: replyTimelinessText(m) },
    { label: "相机→模型 均值", value: fmt(m?.cameraToModelAvgMs, "ms", 1) },
    { label: "模型执行 均/峰", value: avgMax(m?.execAvgMs, m?.execMaxMs) },
    { label: "丢帧率", value: fmt(m?.frameDropPerc, "%", 1) },
    { label: "服务端 接收/预处理", value: s ? `${s.recv.toFixed(1)} / ${s.prep.toFixed(1)} ms` : "—" },
    { label: "服务端 推理/总计", value: s ? `${s.htp.toFixed(1)} / ${s.total.toFixed(1)} ms` : "—" },
  ];
});

/** 单核负载条的颜色：>85% 提示满载 */
function coreBarClass(load: number): string {
  return load >= 85 ? "bg-sl-warn" : "bg-sl-accent";
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="sl-card px-5 py-4">
      <div class="flex items-center gap-2">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
          设备遥测
        </h2>
        <Badge :kind="thermal.kind">{{ thermal.text }}</Badge>
      </div>

      <div class="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        <div v-for="f in fields" :key="f.label" class="min-w-0">
          <div class="text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
            {{ f.label }}
          </div>
          <div class="sl-tabular mt-0.5 truncate text-[13px] text-sl-text-1">
            {{ f.value }}
          </div>
        </div>
      </div>

      <!-- CPU 按核负载：细条形，一眼看出有没有单核打满 -->
      <div v-if="cpuCores.length" class="mt-4 border-t border-sl-border pt-3">
        <div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
          CPU 负载（按核，{{ cpuCores.length }} 核）
        </div>
        <div class="flex items-center gap-1">
          <div v-for="(load, i) in cpuCores" :key="i" class="h-4 flex-1 overflow-hidden rounded-sm bg-sl-surface-3">
            <div
              class="h-full transition-[width] duration-700"
              :class="coreBarClass(load)"
              :style="{ width: `${Math.min(100, Math.max(0, load))}%` }"
            />
          </div>
        </div>
      </div>
    </section>

    <section class="sl-card px-5 py-4">
      <div class="flex items-center gap-2">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
          模型来源
        </h2>
        <Badge :kind="model?.bigEnabled ? 'accent' : 'muted'">
          {{ model?.bigEnabled ? "远程大模型已开启" : "远程大模型未开启" }}
        </Badge>
      </div>
      <div v-if="modelFrames.length" class="mt-3">
        <div class="flex items-center gap-0.5">
          <div
            v-for="(f, i) in modelFrames"
            :key="i"
            class="h-4 flex-1 rounded-sm"
            :class="f === 1 ? 'bg-sl-accent' : 'bg-sl-info'"
          />
        </div>
        <div class="sl-tabular mt-1.5 text-[11px] text-sl-text-3">
          最近 {{ modelFrames.length }} 帧：
          <span class="mr-0.5 inline-block size-2 rounded-sm bg-sl-accent align-baseline" />大模型 {{ bigCount }} ·
          <span class="mr-0.5 inline-block size-2 rounded-sm bg-sl-info align-baseline" />小模型 {{ modelFrames.length - bigCount }}（左旧右新）
        </div>
      </div>
      <div class="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-sl-border pt-3 sm:grid-cols-4">
        <div v-for="f in modelFields" :key="f.label" class="min-w-0">
          <div class="text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">{{ f.label }}</div>
          <div class="sl-tabular mt-0.5 truncate text-[13px] text-sl-text-1">{{ f.value }}</div>
        </div>
      </div>
      <div v-if="!modelFrames.length" class="mt-3 text-[13px] text-sl-text-3">暂无模型帧（未行驶或 modeld 未运行）</div>
    </section>
  </div>
</template>
