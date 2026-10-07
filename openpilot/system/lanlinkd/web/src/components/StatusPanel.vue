<script setup lang="ts">
/** 状态页：设备遥测（CPU/GPU/内存/温度/功耗）+ 模型来源（最近 50 帧大/小模型）+ 横向扭矩自整定。
 *
 * 遥测用页面级 1s 轮询而不是全局 4s 的 pollStatus——那个还承担着
 * paramsVersion 同步，不该被高频触发；这里只读不写 store。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { DeviceStatus, ModelStatus, TorqueStatus } from "@/lib/schema";
import { binRows, sourceText, speedDepHint, torqueHeadline } from "@/lib/torque";
import Badge from "./ui/Badge.vue";

const dev = ref<DeviceStatus | null>(null);
const model = ref<ModelStatus | null>(null);
const torque = ref<TorqueStatus | null>(null);
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    const s = await api.status();
    dev.value = s.device ?? null;
    model.value = s.model ?? null;
    torque.value = s.torque ?? null;
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

const timing = computed(() => model.value?.timing ?? null);

// C4 本机分段（缩进行 = 上一行的组成部分，不参与求和）
const STAGE_ROWS = [
  { key: "capture", label: "取帧（出图→收到）" },
  { key: "warp", label: "warp" },
  { key: "pairWait", label: "配对等待" },
  { key: "encode", label: "硬编" },
  { key: "send", label: "发送（含排队）" },
  { key: "replyWait", label: "等 REPLY" },
  { key: "network", label: "其中网络（−手机）", sub: true },
  { key: "total", label: "合计（出图→收到 REPLY）", strong: true },
] as const;

const SOURCE_ROWS = [
  { key: "linkDown", label: "链路断" },
  { key: "timeout", label: "超时没回" },
  { key: "late", label: "迟到" },
  { key: "zeroOutput", label: "零输出" },
  { key: "warmup", label: "warmup" },
] as const;

const LINK_TEXT: Record<string, string> = {
  connecting: "连接中",
  connected: "已连接",
  blip: "瞬断",
  restart: "服务端重启",
  lost: "已断开",
};

const linkText = computed(() => {
  const s = model.value?.linkState ?? "";
  return LINK_TEXT[s] ?? (s || "—");
});

// ---- 横向扭矩自整定 ----

const torqueLearned = computed(() => torque.value?.learned ?? null);
const torqueHead = computed(() => torqueHeadline(torque.value));
const torqueHint = computed(() => speedDepHint(torque.value));
const torqueBins = computed(() =>
  torqueLearned.value ? binRows(torqueLearned.value.bins, torqueLearned.value.latAccelFactor) : [],
);
const torqueApplicable = computed(() => {
  const ctl = torque.value?.offline.lateralControl;
  return !ctl || ctl === "torque";
});

const torqueFields = computed(() => {
  const l = torqueLearned.value;
  const o = torque.value?.offline;
  return [
    { label: "横向加速度系数", value: fmt(l?.latAccelFactor, "", 3), sub: `出厂 ${fmt(o?.latAccelFactor, "", 3)}` },
    { label: "摩擦", value: fmt(l?.friction, "", 3), sub: `出厂 ${fmt(o?.friction, "", 3)}` },
    { label: "偏置", value: fmt(l?.latAccelOffset, "m/s²", 3), sub: "" },
    { label: "学习进度", value: fmt(l?.calPerc, "%"), sub: `${l?.totalPoints ?? 0} 个点` },
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
        <span v-if="model?.bigEnabled" class="text-[12px] text-sl-text-3">链路：{{ linkText }}</span>
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
      <div v-if="!modelFrames.length" class="mt-3 text-[13px] text-sl-text-3">暂无模型帧（未行驶或 modeld 未运行）</div>

      <div v-if="model?.bigEnabled && timing?.window" class="mt-4 border-t border-sl-border pt-3">
        <div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
          C4 分段耗时（最近 {{ timing.window }} 帧，p50 / p90 ms）
          <span v-if="timing.deadlineMs != null" class="normal-case tracking-normal">· 截止 {{ timing.deadlineMs.toFixed(1) }}</span>
        </div>
        <table v-if="timing.stagesMs" class="sl-tabular w-full text-[12px]">
          <tbody>
            <tr v-for="r in STAGE_ROWS" :key="r.key" :class="'strong' in r ? 'font-semibold text-sl-text-1' : 'text-sl-text-2'">
              <td class="py-0.5" :class="'sub' in r ? 'pl-4 text-sl-text-3' : ''">{{ r.label }}</td>
              <td class="py-0.5 text-right">
                {{ timing.stagesMs[r.key].p50.toFixed(1) }} / {{ timing.stagesMs[r.key].p90.toFixed(1) }}
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-[12px] text-sl-text-3">窗口内没有收到 REPLY</div>
        <div class="sl-tabular mt-2 text-[11px] text-sl-text-3">
          小模型原因：
          <template v-for="(r, i) in SOURCE_ROWS" :key="r.key">{{ i ? " · " : "" }}{{ r.label }} {{ timing.sources[r.key] }}</template>
        </div>
      </div>
    </section>

    <section class="sl-card px-5 py-4">
      <div class="flex flex-wrap items-center gap-2">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
          横向扭矩自整定
        </h2>
        <Badge :kind="torqueHead.tone">{{ torqueHead.text }}</Badge>
        <span v-if="torque && torqueApplicable" class="text-[12px] text-sl-text-3">
          数据：{{ sourceText(torque.source) }}
        </span>
      </div>

      <template v-if="torqueLearned && torqueApplicable">
        <div class="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          <div v-for="f in torqueFields" :key="f.label" class="min-w-0">
            <div class="text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
              {{ f.label }}
            </div>
            <div class="sl-tabular mt-0.5 truncate text-[13px] text-sl-text-1">{{ f.value }}</div>
            <div v-if="f.sub" class="sl-tabular truncate text-[11px] text-sl-text-3">{{ f.sub }}</div>
          </div>
        </div>

        <div v-if="torqueBins.length || torqueHint" class="mt-4 border-t border-sl-border pt-3">
          <div class="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
            按车速分段
            <span class="normal-case tracking-normal">
              · {{ torque?.toggles.SpeedDependentTorqueToggle ? "已开启" : "未开启" }}
            </span>
          </div>
          <div v-if="torqueHint" class="mb-2 text-[12px] text-sl-text-3">{{ torqueHint }}</div>
          <table v-if="torqueBins.length" class="sl-tabular w-full text-[12px]">
            <thead>
              <tr class="text-left text-[10px] uppercase tracking-wider text-sl-text-3">
                <th class="py-1 font-semibold">km/h</th>
                <th class="py-1 text-right font-semibold">系数</th>
                <th class="hidden py-1 text-right font-semibold sm:table-cell">较全局</th>
                <th class="py-1 text-right font-semibold">摩擦</th>
                <th class="w-[38%] py-1 pl-4 font-semibold">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in torqueBins" :key="r.key" class="text-sl-text-2">
                <td class="py-0.5">{{ r.range }}</td>
                <td class="py-0.5 text-right text-sl-text-1">{{ r.latAccelFactor }}</td>
                <td class="hidden py-0.5 text-right text-sl-text-3 sm:table-cell">{{ r.delta }}</td>
                <td class="py-0.5 text-right">{{ r.friction }}</td>
                <td class="py-0.5 pl-4">
                  <div class="flex items-center gap-2">
                    <div class="h-1.5 flex-1 overflow-hidden rounded-sm bg-sl-surface-3">
                      <div
                        class="h-full transition-[width] duration-700"
                        :class="r.tone === 'accent' ? 'bg-sl-accent' : 'bg-sl-warn'"
                        :style="{ width: `${r.progress ?? 0}%` }"
                      />
                    </div>
                    <span class="w-[5.5em] shrink-0 text-right text-[11px]" :class="r.tone === 'accent' ? 'text-sl-accent' : 'text-sl-text-3'">
                      {{ r.state }}
                    </span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="torqueBins.length" class="mt-1.5 text-[11px] text-sl-text-3">
            学习中的档实际使用全局值；已生效的档在行驶中按车速插值使用。
          </div>
        </div>
      </template>
      <div v-else-if="torqueApplicable" class="mt-3 text-[13px] text-sl-text-3">
        暂无学习数据（开启自整定后行驶一段时间，每 60 秒保存一次）
      </div>
    </section>
  </div>
</template>
