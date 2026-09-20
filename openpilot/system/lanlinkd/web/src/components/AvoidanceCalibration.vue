<script setup lang="ts">
/** 横向避让设置区的标定卡片。从 AvoidanceView 迁来——用户要求"开始标定"
 * 放进 设置 → 转向 → 横向避让,且未完成标定不许启用避让。
 *
 * 卡片里有两件都叫"标定"的事,必须分清:
 *  - 在线标定(extrinsicsCalibration,pitch/yaw/roll):openpilot 自动做,
 *    行驶中收敛,没有任何手动步骤。它是启用横向避让的门槛——地平面投影的
 *    dRel 对 pitch 的敏感度在 40m 处是 0.5° → 41%,用未标定的 pitch 会直接
 *    生成虚假偏移,所以开关在收敛前置灰并显示进度。
 *  - CAMERA_TO_FRONT 手工精修:可选,只想精修纵向安装偏移(相机在前保险杠
 *    后方多远)时才跑。配对数据来自避让运行时的 avoidanceDebug 雷达↔视觉
 *    关联,所以它反过来需要避让已经开着。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { CalibrationStatus } from "@/lib/schema";
import { boolValue, store } from "@/lib/store";
import Badge from "./ui/Badge.vue";
import { cn } from "@/lib/utils";

const calib = ref<CalibrationStatus | null>(null);
let calibTimer: ReturnType<typeof setInterval> | undefined;

async function pollCalib(): Promise<void> {
  try {
    calib.value = await api.calibrationStatus();
  } catch {
    /* 会话状态丢失时保持上次值 */
  }
}

async function toggleCalibration(): Promise<void> {
  try {
    calib.value = calib.value?.running
      ? await api.calibrationStop()
      : await api.calibrationStart();
  } catch {
    await pollCalib();
  }
}

onMounted(() => {
  void pollCalib();
  calibTimer = setInterval(() => void pollCalib(), 1000);
});
onUnmounted(() => {
  if (calibTimer) clearInterval(calibTimer);
});

// ---- 在线标定(自动,启用门槛) ----
const cal = computed(() => store.cal);
const onlineLabel = computed(() => {
  if (cal.value.calValid) return "已完成";
  if (cal.value.calStatus === "recalibrating") return `重新标定中 ${cal.value.calPerc}%`;
  if (cal.value.calStatus === "unknown") return "状态未知";
  return `进行中 ${cal.value.calPerc}%`;
});
const onlineHint = computed(() => {
  if (cal.value.calValid) return "相机安装角度已收敛，避让的视觉路径可用。";
  if (cal.value.calStatus === "unknown")
    return "上车行驶后 openpilot 自动开始标定（约几分钟到几十分钟，取决于路况），无需任何手动操作。";
  return "正常行驶即自动收敛——需要车速 > 15 mph 且车道线清晰。完成前上方的启用开关保持置灰。";
});

// ---- CAMERA_TO_FRONT 手工精修(可选) ----
const calibRunning = computed(() => Boolean(calib.value?.running));
const calibResult = computed(() => calib.value?.last_result ?? null);
const calibError = computed(() => calib.value?.last_error ?? null);
const avoidanceOn = computed(() => boolValue("AvoidanceEnabled"));
const manualBlockedReason = computed(() =>
  avoidanceOn.value
    ? ""
    : "需要先开启横向避让——配对数据来自避让运行时的雷达↔视觉关联",
);

const BAND_LABELS: Record<string, string> = {
  le10m: "≤ 10m",
  "10to25m": "10–25m",
  "25to40m": "25–40m",
};

// 空档位必须显示"无数据"而不是通过:全部配对落在 40m 以外的数据集,
// 不该在每一档都显示绿色(聚合判据已拒,前端也不能撒谎)。
const calibBands = computed(() =>
  Object.entries(calibResult.value?.bands ?? {}).map(([key, b]) => ({
    key,
    label: BAND_LABELS[key] ?? key,
    n: b.n,
    p95: b.p95_m,
    bearing: b.bearing_p95_deg,
    verdict: b.pass === null ? "无数据" : b.pass ? "达标" : "未达标",
    kind: b.pass === null ? ("muted" as const) : b.pass ? ("accent" as const) : ("warn" as const),
  })),
);
</script>

<template>
  <div class="mb-3 flex flex-col gap-3 rounded-md bg-sl-surface-2 px-3 py-3 text-[12px]">
    <!-- 在线标定:启用门槛 -->
    <div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold text-sl-text-2">相机在线标定</span>
        <Badge :kind="cal.calValid ? 'accent' : 'info'">{{ onlineLabel }}</Badge>
      </div>
      <p class="mt-1 leading-relaxed text-sl-text-3">{{ onlineHint }}</p>
    </div>

    <!-- CAMERA_TO_FRONT 手工精修 -->
    <div class="border-t border-sl-border/70 pt-3">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span class="font-semibold text-sl-text-2">安装偏移精修（可选）</span>
        <button
          type="button"
          :class="cn(
            'rounded-md px-3 py-1 text-[12px] font-semibold transition-colors',
            calibRunning
              ? 'bg-sl-warn/15 text-sl-warn hover:bg-sl-warn/25'
              : 'bg-sl-accent/15 text-sl-accent hover:bg-sl-accent/25',
            manualBlockedReason && 'pointer-events-none opacity-40',
          )"
          :title="manualBlockedReason"
          @click="toggleCalibration"
        >
          {{ calibRunning ? "停止标定" : "开始标定" }}
        </button>
        <Badge v-if="calibRunning" kind="info">标定中 {{ calib?.n_pairs ?? 0 }} 对</Badge>
        <span v-if="manualBlockedReason" class="text-sl-warn/90">{{ manualBlockedReason }}</span>
      </div>
      <p class="mt-1 leading-relaxed text-sl-text-3">
        精修纵向安装偏移 CAMERA_TO_FRONT（相机在前保险杠后方多远）。默认 1.5m
        对所有标准安装已经正确，通常不需要跑。
      </p>

      <!-- 精修结果 / 错误 -->
      <div v-if="calibError" class="mt-2 rounded-md bg-sl-warn/10 px-3 py-2 text-sl-warn">
        {{ calibError }}
      </div>
      <details v-else-if="calibResult" class="mt-2 rounded-md bg-sl-surface-3 px-3 py-2">
        <summary class="cursor-pointer select-none text-sl-text-2">
          <Badge :kind="calibResult.pass ? 'accent' : 'warn'">
            {{ calibResult.pass ? "标定达标" : "未达标" }}
          </Badge>
          {{ calibResult.n_pairs }} 对 · 残差 p95
          {{ calibResult.residual_p95_before_m.toFixed(3) }} →
          {{ calibResult.residual_p95_after_m.toFixed(3) }} m
          <span v-if="calibResult.insufficient" class="text-sl-warn">
            （配对 &lt; 30，结果仅供参考）
          </span>
        </summary>
        <div class="mt-2 grid gap-1 text-sl-text-3 md:grid-cols-2">
          <span>Δfront {{ calibResult.d_front_m.toFixed(3) }} m</span>
          <span>侧向偏置 {{ calibResult.lateral_bias_m.toFixed(3) }} m</span>
        </div>
        <p class="mt-1 text-[11px] leading-relaxed text-sl-text-3">
          pitch / yaw 已由 openpilot 的在线标定持续维护，这里不再拟合；侧向偏置是
          诊断项——它持续不为零说明相机横向装偏了，应该动硬件而不是改常数。
        </p>
        <div v-if="calibBands.length" class="mt-2">
          <div class="text-sl-text-2">分档残差（25m 以上只看方位角）</div>
          <div
            v-for="b in calibBands"
            :key="b.key"
            class="mt-1 flex items-center gap-2 text-sl-text-3"
          >
            <span class="w-20">{{ b.label }}</span>
            <Badge :kind="b.kind">{{ b.verdict }}</Badge>
            <span v-if="b.n">
              {{ b.n }} 对 ·
              <template v-if="b.p95 !== undefined">p95 {{ b.p95.toFixed(2) }} m · </template>
              方位 {{ b.bearing?.toFixed(2) ?? "—" }}°
            </span>
          </div>
          <p class="mt-1 text-[11px] leading-relaxed text-sl-text-3">
            单一全局阈值在 10m 以外物理不可达（40m 处 0.30m 残差需要 0.013° 的
            pitch 精度，而车辆俯仰变化就有 1° 量级），所以按距离分档。无数据的档
            不计入通过。
          </p>
        </div>
        <div v-for="w in calibResult.warnings" :key="w" class="mt-1 text-sl-warn">{{ w }}</div>
        <pre class="mt-2 overflow-x-auto rounded bg-sl-bg p-2 font-mono text-[11px] leading-relaxed text-sl-text-2">{{ calibResult.constants_block }}</pre>
        <p class="mt-1 text-[11px] text-sl-text-3">
          粘贴到 constants.py 后重编再跑一轮（增量语义，1-2 轮收敛）。
        </p>
      </details>
    </div>
  </div>
</template>
