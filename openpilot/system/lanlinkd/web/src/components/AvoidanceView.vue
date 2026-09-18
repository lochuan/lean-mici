<script setup lang="ts">
/** 避让监测鸟瞰图：双源轮询（/api/radar 250ms 雷达点 + /api/avoidance 500ms
 *  视觉目标），planner 叠加（yDes 箭头 + 幽影车道）。
 *
 * 方向语义（review 裁定）：yDes > 0 = 向左偏（与 yRel 左正同号），
 * 箭头按 yDes 符号画；direction = 障碍物侧（+1 = 障碍在右），只做侧别
 * 标识，不画箭头。
 *
 * 降级链：/api/avoidance stale（avoidanced 未开/未跑）→ 只画雷达点，
 * 等同旧雷达图；/api/radar 也 stale → "等待数据"占位。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { AvoidanceSnapshot, RadarSnapshot } from "@/lib/schema";
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

const RADAR_POLL_MS = 250;
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

// ---- 双源轮询 ----

const radar = ref<RadarSnapshot | null>(null);
const av = ref<AvoidanceSnapshot | null>(null);
const radarFails = ref(0);
const avFails = ref(0);
let radarTimer: ReturnType<typeof setInterval> | undefined;
let avTimer: ReturnType<typeof setInterval> | undefined;

async function pollRadar(): Promise<void> {
  try {
    radar.value = await api.radar();
    radarFails.value = 0;
  } catch {
    radarFails.value += 1; // 失败保留最后一帧
  }
}

async function pollAvoidance(): Promise<void> {
  try {
    av.value = await api.avoidance();
    avFails.value = 0;
  } catch {
    avFails.value += 1;
  }
}

onMounted(() => {
  void pollRadar();
  void pollAvoidance();
  radarTimer = setInterval(() => void pollRadar(), RADAR_POLL_MS);
  avTimer = setInterval(() => void pollAvoidance(), AVOID_POLL_MS);
});
onUnmounted(() => {
  if (radarTimer) clearInterval(radarTimer);
  if (avTimer) clearInterval(avTimer);
});

const radarStale = computed(
  () => !radar.value || Boolean(radar.value.stale) || radarFails.value >= FAIL_LIMIT,
);
const avStale = computed(
  () => !av.value || Boolean(av.value.stale) || avFails.value >= FAIL_LIMIT,
);

const canError = computed(() => Boolean(radar.value?.errors?.canError));

// ---- 雷达点：灰白小圆（avoidance stale 时也能单独撑起整张图）----

const radarPoints = computed(() =>
  radarStale.value ? [] : (radar.value?.points ?? []),
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

const tickY = (d: number) => rangeY(d, VB);
const gridX = (y: number) => lateralX(y, VB);
</script>

<template>
  <section class="sl-card px-5 py-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
        避让监测
      </h2>
      <Badge v-if="radarStale" kind="warn">无数据</Badge>
      <Badge v-else-if="avStale" kind="warn">无避让数据</Badge>
      <Badge v-else-if="planner" :kind="status.badge">{{ status.label }}</Badge>
      <Badge v-if="canError" kind="danger">CAN 错误</Badge>
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

      <!-- 雷达点：灰白小圆 -->
      <g v-if="!radarStale">
        <circle
          v-for="(p, i) in radarPoints"
          :key="p.trackId === -1 ? `i${i}` : p.trackId"
          :cx="projectPoint(p, VB).x"
          :cy="projectPoint(p, VB).y"
          r="4"
          class="fill-sl-text-3 stroke-sl-bg"
          stroke-width="1.5"
        >
          <title>#{{ p.trackId }}  dRel {{ p.dRel.toFixed(1) }}m  yRel {{ p.yRel.toFixed(2) }}m  vRel {{ p.vRel.toFixed(2) }}m/s</title>
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

      <!-- 无数据占位：连雷达都 stale -->
      <text
        v-if="radarStale"
        :x="VB.width / 2"
        :y="VB.height / 2"
        text-anchor="middle"
        class="fill-sl-text-3"
        font-size="13"
      >
        等待数据…（点火且 openpilot 运行后会有 radarTracks / avoidanceDebug）
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
      <span>vEgo <span class="sl-tabular">{{ vEgoKmh }} km/h</span></span>
      <span>路沿余量 <span class="sl-tabular">{{ edgeClearance }}</span></span>
    </div>
  </section>
</template>
