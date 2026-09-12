<script setup lang="ts">
/** ACC 雷达点阵：bus1 radarTracks 的鸟瞰投影（车头朝上）。
 *
 * 250ms 轮询 /api/radar：v1 刻意不用 WebSocket（见 FRONTEND_SPEC 的
 * 传输权衡），4Hz 看点迹分布与 vRel 着色足够。后端 RadarCache 缓存
 * 20Hz 最新帧，所以每次轮询拿到的都是"当前"的点，不欠采样陈旧数据。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { RadarSnapshot } from "@/lib/schema";
import {
  TRACK_FILL,
  TRACK_LABEL,
  lateralX,
  projectPoint,
  rangeTicks,
  rangeY,
  trackBucket,
  type RadarViewBox,
} from "@/lib/radar";
import Badge from "./ui/Badge.vue";

const POLL_MS = 250;
// 连续失败这么多次才显示"无数据"——单次网络抖动不该闪徽章
const FAIL_LIMIT = 3;

const VB: RadarViewBox = {
  width: 720,
  height: 540,
  rangeM: 150,
  lateralM: 10,
  padTop: 24,
  padBottom: 64,
  padX: 28,
};

const RANGE_TICKS = rangeTicks(VB.rangeM, 30); // [0,30,...,150] m
const LATERALS = [-10, -5, 0, 5, 10]; // m，网格线

/** HTML 图例用的背景色（svg 里是 fill-*，span 里是 bg-*） */
const LEGEND_BG = {
  approach: "bg-sl-warn",
  recede: "bg-sl-accent",
  static: "bg-sl-info",
} as const;

const snap = ref<RadarSnapshot | null>(null);
const failures = ref(0);
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    snap.value = await api.radar();
    failures.value = 0;
  } catch {
    // 401 由全局 handler 处理；这里只把点阵判为不可用，保留最后一帧
    failures.value += 1;
  }
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), POLL_MS);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

const points = computed(() => snap.value?.points ?? []);
const stale = computed(
  () => !snap.value || Boolean(snap.value.stale) || failures.value >= FAIL_LIMIT,
);
const canError = computed(() => Boolean(snap.value?.errors?.canError));
const radarUnavailable = computed(() =>
  Boolean(snap.value?.errors?.radarUnavailableTemporary),
);

const projected = computed(() =>
  points.value.map((p) => ({
    p,
    xy: projectPoint(p, VB),
    bucket: trackBucket(p.vRel),
  })),
);

const tickY = (d: number) => rangeY(d, VB);
const gridX = (y: number) => lateralX(y, VB);
const egoX = lateralX(0, VB);
</script>

<template>
  <section class="sl-card px-5 py-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
        ACC 雷达
      </h2>
      <Badge v-if="stale" kind="warn">无数据</Badge>
      <Badge v-else kind="muted">{{ points.length }} 个目标</Badge>
      <Badge v-if="canError" kind="danger">CAN 错误</Badge>
      <Badge v-if="radarUnavailable" kind="warn">雷达暂不可用</Badge>

      <!-- 图例：点色 = 相对速度分桶 -->
      <div class="ml-auto flex items-center gap-3 text-[11px] text-sl-text-3">
        <div v-for="(bg, bucket) in LEGEND_BG" :key="bucket" class="flex items-center gap-1.5">
          <span class="size-2 rounded-full" :class="bg" />
          {{ TRACK_LABEL[bucket] }}
        </div>
      </div>
    </div>

    <svg
      :viewBox="`0 0 ${VB.width} ${VB.height}`"
      class="mt-2 w-full select-none"
      role="img"
      aria-label="ACC 雷达点阵鸟瞰图"
    >
      <!-- 距离网格：0m（车头）在底，150m 在顶 -->
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

      <!-- 横向网格：±10m 覆盖左右各两条车道，中线稍强 -->
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

      <!-- ego：底部中央 -->
      <g :transform="`translate(${egoX}, ${VB.height - VB.padBottom / 2})`">
        <rect
          x="-16" y="-26" width="32" height="52" rx="8"
          class="fill-sl-surface-3 stroke-sl-text-3" stroke-width="1.5"
        />
        <line x1="-10" x2="10" y1="-12" y2="-12" class="stroke-sl-text-3" stroke-width="2" />
        <line x1="-10" x2="10" y1="12" y2="12" class="stroke-sl-text-3" stroke-width="2" />
      </g>

      <!-- 雷达点 -->
      <g v-if="!stale">
        <circle
          v-for="({ p, xy, bucket }, i) in projected"
          :key="p.trackId === -1 ? i : p.trackId"
          :cx="xy.x"
          :cy="xy.y"
          r="5"
          :class="TRACK_FILL[bucket]"
          class="stroke-sl-bg"
          stroke-width="1.5"
        >
          <title>#{{ p.trackId }}  dRel {{ p.dRel.toFixed(1) }}m  yRel {{ p.yRel.toFixed(2) }}m  vRel {{ p.vRel.toFixed(2) }}m/s</title>
        </circle>
      </g>

      <!-- 无数据时的占位说明 -->
      <text
        v-if="stale"
        :x="VB.width / 2"
        :y="VB.height / 2"
        text-anchor="middle"
        class="fill-sl-text-3"
        font-size="13"
      >
        等待雷达数据…（点火且 openpilot 运行后 card 会发布 radarTracks）
      </text>
    </svg>
  </section>
</template>
