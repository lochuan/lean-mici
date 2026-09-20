<script setup lang="ts">
/** 避让监测鸟瞰图：单源轮询 /api/avoidance（500ms），雷达点与视觉目标
 *  都来自 avoidanceDebug targets（vision=false 是雷达点，vision=true 是
 *  YOLO 投影目标），planner 叠加（yDes 箭头 + 幽影车道）。
 *
 * 方向语义（review 裁定）：yDes > 0 = 向左偏（与 yRel 左正同号），
 * 箭头按 yDes 符号画；direction = 障碍物侧（+1 = 障碍在右），只做侧别
 * 标识，不画箭头。
 *
 * 降级链：avoidance 快照 stale（avoidanced 未跑/未开）→ "等待数据"占位；
 * CAN 错误 / 雷达暂不可用徽章来自快照透传的 radarTracks.errors。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { AvoidanceSnapshot, CalibrationStatus } from "@/lib/schema";
import {
  CLS_FILL,
  CLS_LABEL,
  avoidanceStatus,
  fmtEdgeClearance,
  obstacleSide,
  offsetArrow,
  pairMembers,
  targetCls,
  type TargetCls,
} from "@/lib/avoidance";
import {
  lateralX,
  projectPoint,
  rangeTicks,
  rangeY,
  type RadarViewBox,
} from "@/lib/radar";
import Badge from "./ui/Badge.vue";
import { cn } from "@/lib/utils";

const AVOID_POLL_MS = 500;
// 连续失败这么多次才判无数据——单次网络抖动不该闪徽章
const FAIL_LIMIT = 3;

const VB: RadarViewBox = {
  width: 720,
  height: 540,
  rangeM: 50,
  lateralM: 10,
  padTop: 24,
  padBottom: 64,
  padX: 28,
};

const RANGE_TICKS = rangeTicks(VB.rangeM, 10); // [0,10,...,50] m
const LATERALS = [-10, -5, 0, 5, 10]; // m，网格线

/** 图例：视觉目标类别色（雷达点固定灰白） */
const LEGEND: Array<{ cls: TargetCls; bg: string }> = [
  { cls: "person", bg: "bg-sl-danger" },
  { cls: "bicycle", bg: "bg-sl-warn" },
  { cls: "car", bg: "bg-sl-info" },
];

// ---- 单源轮询 ----

const av = ref<AvoidanceSnapshot | null>(null);
const avFails = ref(0);
let avTimer: ReturnType<typeof setInterval> | undefined;

async function pollAvoidance(): Promise<void> {
  try {
    av.value = await api.avoidance();
    avFails.value = 0;
  } catch {
    avFails.value += 1;
  }
}

onMounted(() => {
  void pollAvoidance();
  avTimer = setInterval(() => void pollAvoidance(), AVOID_POLL_MS);
});
onUnmounted(() => {
  if (avTimer) clearInterval(avTimer);
});

const avStale = computed(
  () => !av.value || Boolean(av.value.stale) || avFails.value >= FAIL_LIMIT,
);

const canError = computed(() => !avStale.value && Boolean(av.value?.canError));
const radarUnavailable = computed(
  () => !avStale.value && Boolean(av.value?.radarUnavailable),
);

// ---- 雷达点：灰白小圆（targets 里 vision=false 的条目）----

const radarPoints = computed(() =>
  avStale.value
    ? []
    : (av.value?.targets ?? []).filter((t) => !t.vision),
);

// ---- 视觉目标：类别色方块（targets 里 vision=true 的条目）----

const egoX = lateralX(0, VB);
const egoY = VB.height - VB.padBottom / 2;

const visionTargets = computed(() =>
  avStale.value
    ? []
    : (av.value?.targets ?? [])
        .filter((t) => t.vision)
        .map((t) => ({ t, xy: projectPoint(t, VB), cls: targetCls(t.cls) })),
);

/** 配对连线：matched 的视觉目标与雷达点共享 pairId（0=未配对） */
const pairLines = computed(() => {
  if (avStale.value) return [];
  return pairMembers(av.value?.targets ?? []).map(({ vision, radar: pt }, i) => ({
    a: projectPoint(vision, VB),
    b: projectPoint(pt, VB),
    key: i,
  }));
});

// ---- planner 叠加（avoidance 数据有效时才画）----

const planner = computed(() => (!avStale.value && av.value?.valid ? av.value : null));

const arrow = computed(() =>
  planner.value ? offsetArrow(planner.value.yDes ?? 0, VB, egoX) : null,
);

/** 箭头头部小三角（yDes≈0 时不画，避免零长度箭头） */
const arrowHead = computed(() => {
  if (!arrow.value || Math.abs(arrow.value.yDes) < 0.05) return "";
  const rightward = arrow.value.x2 >= arrow.value.x1;
  const tip = arrow.value.x2 + (rightward ? 7 : -7);
  const y = VB.height - 30;
  return `${tip},${y} ${arrow.value.x2},${y - 4.5} ${arrow.value.x2},${y + 4.5}`;
});

const ghostOffset = computed(() => (arrow.value ? arrow.value.x2 - egoX : 0));

// ---- 状态条 ----

const status = computed(() =>
  avoidanceStatus({
    valid: av.value?.valid,
    active: av.value?.active,
    stale: avStale.value,
  }),
);

const counts = computed(() => ({
  r: av.value?.nRadar ?? 0,
  v: av.value?.nVision ?? 0,
  a: av.value?.nAssociated ?? 0,
}));

// 视觉路径被标定门关掉时，V 会恒为 0。不解释的话这看起来像视觉坏了，而实际
// 上是 avoidanced 有意关掉的：地平面投影的距离对 pitch 极度敏感（40m 处 0.5°
// 误差 = 41%），用未标定的 pitch 会直接生成虚假偏移。
const visionGated = computed(() => Boolean(av.value?.visionGated) && !avStale.value);

const visionGatedWhy = computed(() => {
  const status = av.value?.calStatus ?? "unknown";
  const perc = av.value?.calPerc ?? 0;
  if (status === "uncalibrated" || status === "recalibrating") {
    return `相机标定未完成（${status === "recalibrating" ? "重新标定中" : "未标定"} ${perc}%），视觉路径已关闭，当前为纯雷达避让。继续正常驾驶让 openpilot 完成标定即可——静止目标需要视觉确认，所以此期间只对运动目标避让。`;
  }
  return `相机标定状态为 ${status}，视觉路径已关闭，当前为纯雷达避让。`;
});

const fmtYDes = computed(() => {
  const v = planner.value?.yDes;
  return v === undefined ? "—" : `${v.toFixed(2)} m`;
});

const fmtBias = computed(() => {
  const v = planner.value?.bias;
  return v === undefined || !Number.isFinite(v) ? "—" : v.toFixed(3);
});

const vEgoKmh = computed(() => {
  const v = planner.value?.vEgo;
  return v === undefined || !Number.isFinite(v) ? "—" : (v * 3.6).toFixed(0);
});

const edgeClearance = computed(() => fmtEdgeClearance(planner.value?.edgeClearance));

const bsmLeft = computed(() => Boolean(planner.value?.bsmLeft));
const bsmRight = computed(() => Boolean(planner.value?.bsmRight));

const sideLabel = computed(() => obstacleSide(av.value?.direction));

// ---- 在线标定会话（收集 avoidanceDebug 配对目标，停止时拟合投影常数）----

const calib = ref<CalibrationStatus | null>(null);
let calibTimer: ReturnType<typeof setInterval> | undefined;

async function pollCalib(): Promise<void> {
  try {
    calib.value = await api.calibrationStatus();
  } catch {
    // 端点不可用（旧后端）时静默——按钮功能随之不可用
  }
}

async function toggleCalibration(): Promise<void> {
  try {
    calib.value = calib.value?.running
      ? await api.calibrationStop()
      : await api.calibrationStart();
  } catch {
    // 409 等：忽略，下一轮轮询会校正状态
  }
}

onMounted(() => {
  void pollCalib();
  calibTimer = setInterval(() => void pollCalib(), 1000);
});
onUnmounted(() => {
  if (calibTimer) clearInterval(calibTimer);
});

const calibRunning = computed(() => Boolean(calib.value?.running));
const calibResult = computed(() => calib.value?.last_result ?? null);
const calibError = computed(() => calib.value?.last_error ?? null);

// 分档残差。后端的 pass 为 null 表示该档没有配对 —— 必须显示成「无数据」而不是
// 通过，否则一份全部落在 40m 以外的数据会让每一档都显示绿色。
const BAND_LABELS: Record<string, string> = {
  le10m: "≤ 10m",
  "10to25m": "10–25m",
  "25to40m": "25–40m",
};

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

const tickY = (d: number) => rangeY(d, VB);
const gridX = (y: number) => lateralX(y, VB);
</script>

<template>
  <section class="sl-card px-5 py-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
        避让监测
      </h2>
      <Badge v-if="avStale" kind="warn">无数据</Badge>
      <Badge v-else-if="planner" :kind="status.badge">{{ status.label }}</Badge>
      <Badge v-if="canError" kind="danger">CAN 错误</Badge>
      <Badge v-if="radarUnavailable" kind="warn">雷达暂不可用</Badge>
      <Badge v-if="!avStale && sideLabel !== '—'" kind="muted">{{ sideLabel }}</Badge>

      <!-- 图例：雷达点灰白 + 视觉目标类别色 -->
      <div class="ml-auto flex items-center gap-3 text-[11px] text-sl-text-3">
        <div class="flex items-center gap-1.5">
          <span class="size-2 rounded-full bg-sl-text-3" /> 雷达点
        </div>
        <div v-for="l in LEGEND" :key="l.cls" class="flex items-center gap-1.5">
          <span class="size-2 rounded-sm" :class="l.bg" />
          {{ CLS_LABEL[l.cls] }}
        </div>
      </div>
    </div>

    <svg
      :viewBox="`0 0 ${VB.width} ${VB.height}`"
      class="mt-2 w-full select-none"
      role="img"
      aria-label="避让监测鸟瞰图"
    >
      <!-- 距离网格：0m（车头）在底，50m 在顶 -->
      <g
        v-for="d in RANGE_TICKS"
        :key="`r${d}`"
        stroke-dasharray="3 5"
        class="stroke-sl-border/70"
      >
        <line :x1="VB.padX" :x2="VB.width - VB.padX" :y1="tickY(d)" :y2="tickY(d)" />
      </g>
      <g
        v-for="d in RANGE_TICKS"
        :key="`rt${d}`"
        class="fill-sl-text-3 font-mono"
        font-size="10"
        text-anchor="start"
      >
        <text :x="VB.padX + 4" :y="tickY(d) - 4">{{ d }}m</text>
      </g>

      <!-- 横向网格：±10m，中线稍强 -->
      <line
        v-for="y in LATERALS"
        :key="`l${y}`"
        :x1="gridX(y)"
        :x2="gridX(y)"
        :y1="VB.padTop"
        :y2="VB.height - VB.padBottom"
        stroke-dasharray="3 5"
        :class="y === 0 ? 'stroke-sl-border' : 'stroke-sl-border/40'"
      />

      <!-- ego：底部中央；幽影虚线框 = 目标车道位置（planner 有效时） -->
      <g :transform="`translate(${egoX}, ${egoY})`">
        <rect
          x="-16" y="-26" width="32" height="52" rx="8"
          class="fill-sl-surface-3 stroke-sl-text-3" stroke-width="1.5"
        />
        <line x1="-10" x2="10" y1="-12" y2="-12" class="stroke-sl-text-3" stroke-width="2" />
        <line x1="-10" x2="10" y1="12" y2="12" class="stroke-sl-text-3" stroke-width="2" />
        <rect
          v-if="ghostOffset"
          :x="-16" :y="-26" width="32" height="52" rx="8"
          class="fill-transparent stroke-sl-accent" stroke-width="1.5" stroke-dasharray="4 4"
          :transform="`translate(${ghostOffset}, 0)`"
        />
      </g>

      <!-- 配对连线：雷达点 ↔ 视觉目标 -->
      <line
        v-for="l in pairLines"
        :key="`p${l.key}`"
        :x1="l.a.x" :y1="l.a.y" :x2="l.b.x" :y2="l.b.y"
        class="stroke-sl-accent" stroke-width="1" stroke-dasharray="2 3"
      />

      <!-- 雷达点：灰白小圆（来自 avoidance targets vision=false） -->
      <g v-if="!avStale">
        <circle
          v-for="(p, i) in radarPoints"
          :key="`r${p.pairId || `i${i}`}`"
          :cx="projectPoint(p, VB).x"
          :cy="projectPoint(p, VB).y"
          r="4"
          class="fill-sl-text-3 stroke-sl-bg"
          stroke-width="1.5"
        >
          <title>雷达点  dRel {{ p.dRel.toFixed(1) }}m  yRel {{ p.yRel.toFixed(2) }}m  vRel {{ p.vRel.toFixed(2) }}m/s</title>
        </circle>
      </g>

      <!-- 视觉目标：类别色方块；inGate=false 降透明度；matched 加环 -->
      <g v-if="!avStale">
        <g v-for="(v, i) in visionTargets" :key="`v${i}`" :opacity="v.t.inGate ? 1 : 0.35">
          <rect
            :x="v.xy.x - 6" :y="v.xy.y - 6" width="12" height="12" rx="2"
            :class="CLS_FILL[v.cls]"
            class="stroke-sl-bg" stroke-width="1.5"
          />
          <rect
            v-if="v.t.matched"
            :x="v.xy.x - 9" :y="v.xy.y - 9" width="18" height="18" rx="4"
            class="fill-none stroke-sl-text-1" stroke-width="1"
          />
          <title>{{ CLS_LABEL[v.cls] }}  conf {{ (v.t.conf * 100).toFixed(0) }}%  dRel {{ v.t.dRel.toFixed(1) }}m  yRel {{ v.t.yRel.toFixed(2) }}m</title>
        </g>
      </g>

      <!-- planner 偏移箭头：yDes 方向 + 幅度标签 -->
      <g v-if="arrow">
        <line
          :x1="arrow.x1" :x2="arrow.x2"
          :y1="VB.height - 30" :y2="VB.height - 30"
          class="stroke-sl-accent" stroke-width="2.5"
        />
        <polygon v-if="arrowHead" :points="arrowHead" class="fill-sl-accent" />
        <text
          :x="(arrow.x1 + arrow.x2) / 2"
          :y="VB.height - 36"
          text-anchor="middle"
          class="fill-sl-accent font-mono"
          font-size="11"
        >
          {{ arrow.yDes.toFixed(2) }} m
        </text>
      </g>

      <!-- 无数据占位：avoidanced 未运行或未收到 avoidanceDebug -->
      <text
        v-if="avStale"
        :x="VB.width / 2"
        :y="VB.height / 2"
        text-anchor="middle"
        class="fill-sl-text-3"
        font-size="13"
      >
        等待数据…（点火且 avoidanced 运行后会发布 avoidanceDebug）
      </text>
    </svg>

    <!-- 状态条 -->
    <div
      v-if="!avStale"
      class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-sl-text-2"
    >
      <Badge :kind="status.badge">{{ status.label }}</Badge>
      <span>yDes <span class="sl-tabular">{{ fmtYDes }}</span></span>
      <span>bias <span class="sl-tabular">{{ fmtBias }} 1/m</span></span>
      <Badge :kind="bsmLeft ? 'warn' : 'muted'">BSM 左</Badge>
      <Badge :kind="bsmRight ? 'warn' : 'muted'">BSM 右</Badge>
      <span class="text-sl-text-3">R {{ counts.r }} · V {{ counts.v }} · A {{ counts.a }}</span>
      <Badge v-if="visionGated" kind="warn" :title="visionGatedWhy">视觉已关</Badge>
      <span>vEgo <span class="sl-tabular">{{ vEgoKmh }} km/h</span></span>
      <span>路沿余量 <span class="sl-tabular">{{ edgeClearance }}</span></span>
    </div>

    <!-- 视觉被标定门关掉时解释 V=0：否则「视觉一直是 0」看起来像坏了 -->
    <div
      v-if="visionGated"
      class="mt-1 rounded-md bg-sl-warn/10 px-3 py-2 text-[12px] text-sl-warn"
    >
      {{ visionGatedWhy }}
    </div>

    <!-- 在线标定会话（始终可见：avoidanced 未运行时给出开启提示） -->
    <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px]">
      <button
        type="button"
        :class="cn(
          'rounded-md px-3 py-1 text-[12px] font-semibold transition-colors',
          calibRunning
            ? 'bg-sl-warn/15 text-sl-warn hover:bg-sl-warn/25'
            : 'bg-sl-accent/15 text-sl-accent hover:bg-sl-accent/25',
        )"
        @click="toggleCalibration"
      >
        {{ calibRunning ? "停止标定" : "开始标定" }}
      </button>
      <Badge v-if="calibRunning" kind="info">标定中 {{ calib?.n_pairs ?? 0 }} 对</Badge>
      <span v-if="avStale" class="text-sl-text-3">
        avoidanced 未运行——先到 设置 → 转向 → 横向避让 开启后才有配对数据
      </span>
    </div>

    <!-- 标定结果 / 错误 -->
    <div v-if="calibError" class="mt-2 rounded-md bg-sl-warn/10 px-3 py-2 text-[12px] text-sl-warn">
      {{ calibError }}
    </div>
    <details v-else-if="calibResult" class="mt-2 rounded-md bg-sl-surface-2 px-3 py-2 text-[12px]">
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
      <div class="mt-2 grid gap-1 text-sl-text-3 md:grid-cols-3">
        <span>Δfront {{ calibResult.d_front_m.toFixed(3) }} m</span>
        <span>侧向偏置 {{ calibResult.lateral_bias_m.toFixed(3) }} m</span>
      </div>
      <p class="mt-1 text-[11px] text-sl-text-3">
        pitch / yaw 已由 openpilot 的在线标定持续维护，不再手工拟合；这里只标
        CAMERA_TO_FRONT（在线标定不提供的纵向安装偏移）。侧向偏置是诊断项——它
        持续不为零说明相机横向装偏了，应该动硬件而不是改常数。
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
        <p class="mt-1 text-[11px] text-sl-text-3">
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
  </section>
</template>
