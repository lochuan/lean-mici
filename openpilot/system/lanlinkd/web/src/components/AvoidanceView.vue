<script setup lang="ts">
/** 避让监测鸟瞰图：车道态势视图（Tesla 风格简化版，2026-09-24 spec）。
 *
 *  数据：/api/avoidance 单源轮询（500ms）。目标只画融合对象（vision=true，
 *  配对目标用雷达测距、视觉类别）；未关联雷达点按设计移除。
 *  车道层来自后端 lanes（lanlinkd 订阅 modelV2，lanes.py）：本车道边界
 *  实/虚线由 eagleDebug 的 laneLeft/RightValid（eagled C7 门结论）裁决；
 *  外侧线透明度随 prob；路沿粗琥珀线；邻道底色按变道清空/BSM 着色。
 *
 *  方向语义：yDes > 0 = 向左偏（与 yRel 左正同号），箭头按 yDes 符号画；
 *  direction = 障碍物侧（+1 = 障碍在右），只做侧别标识。
 *  降级链：快照 stale → "等待数据"占位；lanes 为 null → 不画车道层。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import type { AvoidanceSnapshot, LaneLineSnap, LaneSnapshot } from "@/lib/schema";
import {
  CLS_FILL,
  CLS_LABEL,
  avoidanceStatus,
  fmtBudget,
  fmtEdgeClearance,
  laneLabel,
  obstacleSide,
  offsetArrow,
  targetCls,
  type TargetCls,
} from "@/lib/avoidance";
import {
  egoCarRect,
  fusedTargets,
  laneFillD,
  lanePathD,
  LIDX_LEFT,
  LIDX_OUTER_LEFT,
  LIDX_OUTER_RIGHT,
  LIDX_RIGHT,
  outerLineOpacity,
  shapePx,
  targetDisplayPosition,
  targetShape,
} from "@/lib/lanes";
import { lateralX, projectPoint, rangeTicks, rangeY, type RadarViewBox } from "@/lib/radar";
import Badge from "./ui/Badge.vue";


const AVOID_POLL_MS = 500;
// 连续失败这么多次才判无数据——单次网络抖动不该闪徽章
const FAIL_LIMIT = 3;

const VB: RadarViewBox = {
  width: 720,
  height: 540,
  rangeM: 60,
  lateralM: 8,
  padTop: 24,
  padBottom: 64,
  padX: 28,
};

const RANGE_TICKS = rangeTicks(VB.rangeM, 20); // 20m 一档降噪

/** 图例：目标类别色 */
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

// ---- 车道层（lanes 为 null 时整层不画） ----

const lanes = computed<LaneSnapshot | null>(() => (avStale.value ? null : (av.value?.lanes ?? null)));
const grid = computed<number[]>(() => lanes.value?.x ?? Array.from({ length: 13 }, (_, i) => i * 5));

const lineAt = (idx: number): LaneLineSnap | null => lanes.value?.laneLines?.[idx] ?? null;
const edgeAt = (idx: number): LaneLineSnap | null => lanes.value?.roadEdges?.[idx] ?? null;
const laneD = (l: LaneLineSnap | null) => lanePathD(l, grid.value, VB);
const fillD = (a: LaneLineSnap | null, b: LaneLineSnap | null) => laneFillD(a, b, grid.value, VB);
const fillL = (a: LaneLineSnap | null, b: LaneLineSnap | null) => fillD(a, b) ?? undefined;
const pathD = computed<string | undefined>(() => lanePathD(lanes.value?.path ?? null, grid.value, VB) ?? undefined);

// 外侧车道线透明度：prob<0.3/未知不画（lib/lanes.outerLineOpacity）
const outerLeftStyle = computed(() => {
  const o = outerLineOpacity(lineAt(LIDX_OUTER_LEFT));
  return o === null ? undefined : { opacity: o.toFixed(2) };
});
const outerRightStyle = computed(() => {
  const o = outerLineOpacity(lineAt(LIDX_OUTER_RIGHT));
  return o === null ? undefined : { opacity: o.toFixed(2) };
});

// ---- 状态推导 ----

const status = computed(() =>
  avoidanceStatus({
    valid: av.value?.valid,
    active: av.value?.active,
    stale: avStale.value,
  }),
);

const planner = computed(() => (!avStale.value && av.value?.valid ? av.value : null));

const bsmLeft = computed(() => Boolean(planner.value?.bsmLeft));
const bsmRight = computed(() => Boolean(planner.value?.bsmRight));
const changeClearLeft = computed(() => (!avStale.value ? av.value?.changeClearLeft : undefined));
const changeClearRight = computed(() => (!avStale.value ? av.value?.changeClearRight : undefined));

/** 邻道底色：BSM 报警整道红；变道清空 通=绿 / 拦=红 / 未知=中性 */
function adjacentFillClass(bsm: boolean, clear: boolean | undefined): string {
  if (bsm) return "fill-sl-danger/20";
  if (clear === true) return "fill-sl-accent/10";
  if (clear === false) return "fill-sl-danger/12";
  return "fill-sl-surface-3/40";
}
const adjacentLeftFill = computed(() => adjacentFillClass(bsmLeft.value, changeClearLeft.value));
const adjacentRightFill = computed(() => adjacentFillClass(bsmRight.value, changeClearRight.value));

const laneLeftValid = computed(() => !avStale.value && av.value?.laneLeftValid === true);
const laneRightValid = computed(() => !avStale.value && av.value?.laneRightValid === true);
const budgetLeft = computed(() => (avStale.value ? undefined : av.value?.budgetLeft));
const budgetRight = computed(() => (avStale.value ? undefined : av.value?.budgetRight));

// 视觉路径被标定门关掉时目标会恒为 0。不解释的话这看起来像视觉坏了，而实际
// 上是 eagled 有意关掉的：地平面投影的距离对 pitch 极度敏感（40m 处 0.5°
// 误差 = 41%），用未标定的 pitch 会直接生成虚假偏移。
const visionGated = computed(() => Boolean(av.value?.visionGated) && !avStale.value);
const visionGatedWhy = computed(() => {
  const calStatus = av.value?.calStatus ?? "unknown";
  const perc = av.value?.calPerc ?? 0;
  if (calStatus === "uncalibrated" || calStatus === "recalibrating") {
    return `相机标定未完成（${calStatus === "recalibrating" ? "重新标定中" : "未标定"} ${perc}%），视觉路径已关闭，当前为纯雷达避让。继续正常驾驶让 openpilot 完成标定即可——静止目标需要视觉确认，所以此期间只对运动目标避让。`;
  }
  return `相机标定状态为 ${calStatus}，视觉路径已关闭，当前为纯雷达避让。`;
});

// ---- 目标（仅融合对象；配对目标用雷达测距、视觉类别） ----

const displayTargets = computed(() => {
  if (avStale.value) return [];
  const all = av.value?.targets ?? [];
  return fusedTargets(all).map((t) => ({
    t,
    pos: projectPoint(targetDisplayPosition(t, all), VB),
    cls: targetCls(t.cls),
    shape: shapePx(targetShape(t.cls), VB),
  }));
});

const ego = egoCarRect(VB);

const arrow = computed(() =>
  planner.value ? offsetArrow(planner.value.yDes ?? 0, VB, ego.cx) : null,
);

/** 箭头头部小三角（yDes≈0 时不画，避免零长度箭头） */
const arrowHead = computed(() => {
  if (!arrow.value || Math.abs(arrow.value.yDes) < 0.05) return "";
  const rightward = arrow.value.x2 >= arrow.value.x1;
  const tip = arrow.value.x2 + (rightward ? 7 : -7);
  const y = VB.height - 14;
  return `${tip},${y} ${arrow.value.x2},${y - 4.5} ${arrow.value.x2},${y + 4.5}`;
});

const ghostOffset = computed(() => (arrow.value ? arrow.value.x2 - ego.cx : 0));

// ---- 状态条数值 ----

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
const targetCount = computed(() => av.value?.nVision ?? 0);
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
      <Badge v-if="avStale" kind="warn">无数据</Badge>
      <Badge v-else-if="planner" :kind="status.badge">{{ status.label }}</Badge>
      <Badge v-if="canError" kind="danger">CAN 错误</Badge>
      <Badge v-if="radarUnavailable" kind="warn">雷达暂不可用</Badge>
      <Badge v-if="!avStale && sideLabel !== '—'" kind="muted">{{ sideLabel }}</Badge>

      <!-- 图例：目标类别色 + 路沿 -->
      <div class="ml-auto flex items-center gap-3 text-[11px] text-sl-text-3">
        <div v-for="l in LEGEND" :key="l.cls" class="flex items-center gap-1.5">
          <span class="size-2 rounded-sm" :class="l.bg" />
          {{ CLS_LABEL[l.cls] }}
        </div>
        <div class="flex items-center gap-1.5">
          <span class="h-0.5 w-3 bg-sl-warn" /> 路沿
        </div>
      </div>
    </div>

    <svg
      :viewBox="`0 0 ${VB.width} ${VB.height}`"
      class="mt-2 w-full select-none"
      role="img"
      aria-label="避让监测鸟瞰图"
    >
      <!-- 车道底色：本道蓝，邻道按变道清空/BSM 着色 -->
      <path v-if="fillL(lineAt(LIDX_OUTER_LEFT), lineAt(LIDX_LEFT))" :d="fillL(lineAt(LIDX_OUTER_LEFT), lineAt(LIDX_LEFT))" :class="adjacentLeftFill" />
      <path v-if="fillL(lineAt(LIDX_LEFT), lineAt(LIDX_RIGHT))" :d="fillL(lineAt(LIDX_LEFT), lineAt(LIDX_RIGHT))" class="fill-sl-accent/10" />
      <path v-if="fillL(lineAt(LIDX_RIGHT), lineAt(LIDX_OUTER_RIGHT))" :d="fillL(lineAt(LIDX_RIGHT), lineAt(LIDX_OUTER_RIGHT))" :class="adjacentRightFill" />

      <!-- 距离网格：0m（车头）在底，60m 在顶；20m 一档降噪 -->
      <g
        v-for="d in RANGE_TICKS"
        :key="`r${d}`"
        stroke-dasharray="3 5"
        class="stroke-sl-border/60"
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

      <!-- 中线（yRel=0） -->
      <line
        :x1="gridX(0)" :x2="gridX(0)"
        :y1="VB.padTop" :y2="VB.height - VB.padBottom"
        stroke-dasharray="3 5"
        class="stroke-sl-border/70"
      />

      <!-- 本车预测路径（modelV2 path） -->
      <path
        v-if="pathD"
        :d="pathD"
        class="fill-none stroke-sl-text-2"
        stroke-width="2"
        stroke-dasharray="6 6"
        stroke-linecap="round"
      />

      <!-- 路沿：粗琥珀线 -->
      <path
        v-show="laneD(edgeAt(0))"
        :d="laneD(edgeAt(0)) ?? undefined"
        class="fill-none stroke-sl-warn"
        stroke-width="3"
        stroke-linecap="round"
        opacity="0.8"
      />
      <path
        v-show="laneD(edgeAt(1))"
        :d="laneD(edgeAt(1)) ?? undefined"
        class="fill-none stroke-sl-warn"
        stroke-width="3"
        stroke-linecap="round"
        opacity="0.8"
      />

      <!-- 外侧车道线：细线，透明度随 prob（<0.3/未知不画） -->
      <path
        v-if="laneD(lineAt(LIDX_OUTER_LEFT)) && outerLeftStyle"
        :d="laneD(lineAt(LIDX_OUTER_LEFT)) ?? undefined"
        class="fill-none stroke-sl-text-3"
        stroke-width="1"
        :style="outerLeftStyle"
      />
      <path
        v-if="laneD(lineAt(LIDX_OUTER_RIGHT)) && outerRightStyle"
        :d="laneD(lineAt(LIDX_OUTER_RIGHT)) ?? undefined"
        class="fill-none stroke-sl-text-3"
        stroke-width="1"
        :style="outerRightStyle"
      />

      <!-- 本车道边界：可信=白实线，不可信=灰虚线（eagled C7 门结论） -->
      <path
        v-if="laneD(lineAt(LIDX_LEFT))"
        :d="laneD(lineAt(LIDX_LEFT)) ?? undefined"
        :class="laneLeftValid ? 'stroke-sl-text-1' : 'stroke-sl-text-3'"
        fill="none"
        stroke-width="2.5"
        :stroke-dasharray="laneLeftValid ? undefined : '6 6'"
        stroke-linecap="round"
      />
      <path
        v-if="laneD(lineAt(LIDX_RIGHT))"
        :d="laneD(lineAt(LIDX_RIGHT)) ?? undefined"
        :class="laneRightValid ? 'stroke-sl-text-1' : 'stroke-sl-text-3'"
        fill="none"
        stroke-width="2.5"
        :stroke-dasharray="laneRightValid ? undefined : '6 6'"
        stroke-linecap="round"
      />

      <!-- 目标：类别定形（car 矩形/person 圆/bike 窄条），inGate 红描边 -->
      <g v-if="!avStale">
        <g v-for="(v, i) in displayTargets" :key="`v${i}`" :opacity="v.t.inGate ? 1 : 0.4">
          <rect
            v-if="v.shape.kind === 'rect'"
            :x="v.pos.x - v.shape.wPx / 2" :y="v.pos.y - v.shape.hPx / 2"
            :width="v.shape.wPx" :height="v.shape.hPx" rx="4"
            :class="[CLS_FILL[v.cls], v.t.inGate ? 'stroke-sl-danger' : 'stroke-sl-bg']"
            :stroke-width="v.t.inGate ? 2.5 : 1.5"
          />
          <circle
            v-else
            :cx="v.pos.x" :cy="v.pos.y" :r="v.shape.wPx / 2"
            :class="[CLS_FILL[v.cls], v.t.inGate ? 'stroke-sl-danger' : 'stroke-sl-bg']"
            :stroke-width="v.t.inGate ? 2.5 : 1.5"
          />
          <title>{{ CLS_LABEL[v.cls] }}  conf {{ (v.t.conf * 100).toFixed(0) }}%  dRel {{ v.t.dRel.toFixed(1) }}m  yRel {{ v.t.yRel.toFixed(2) }}m  {{ laneLabel(v.t.lane) }}</title>
        </g>
      </g>

      <!-- ego：车形（横向真实比例 + 纵向视觉拉长）+ 目标车道幽影 -->
      <g :transform="`translate(${ego.cx}, ${ego.cy})`">
        <rect
          :x="-ego.wPx / 2" :y="-ego.hPx / 2" :width="ego.wPx" :height="ego.hPx" rx="9"
          class="fill-sl-surface-3 stroke-sl-text-2" stroke-width="2"
        />
        <line
          :x1="-ego.wPx / 2 + 6" :x2="ego.wPx / 2 - 6"
          :y1="-ego.hPx * 0.18" :y2="-ego.hPx * 0.18"
          class="stroke-sl-text-3" stroke-width="2.5"
        />
        <rect
          v-if="ghostOffset"
          :x="-ego.wPx / 2" :y="-ego.hPx / 2" :width="ego.wPx" :height="ego.hPx" rx="9"
          class="fill-transparent stroke-sl-accent" stroke-width="1.5" stroke-dasharray="4 4"
          :transform="`translate(${ghostOffset}, 0)`"
        />
      </g>

      <!-- planner 偏移箭头：yDes 方向 + 幅度标签（车底下沿） -->
      <g v-if="arrow">
        <line
          :x1="arrow.x1" :x2="arrow.x2"
          :y1="VB.height - 10" :y2="VB.height - 10"
          class="stroke-sl-accent" stroke-width="2.5"
        />
        <polygon v-if="arrowHead" :points="arrowHead" class="fill-sl-accent" />
        <text
          :x="(arrow.x1 + arrow.x2) / 2"
          :y="VB.height - 16"
          text-anchor="middle"
          class="fill-sl-accent font-mono"
          font-size="11"
        >
          {{ arrow.yDes.toFixed(2) }} m
        </text>
      </g>

      <!-- 无数据占位：eagled 未运行或未收到 eagleDebug -->
      <text
        v-if="avStale"
        :x="VB.width / 2"
        :y="VB.height / 2"
        text-anchor="middle"
        class="fill-sl-text-3"
        font-size="13"
      >
        等待数据…（点火且 eagled 运行后会发布 eagleDebug）
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
      <span class="text-sl-text-3">目标 <span class="sl-tabular">{{ targetCount }}</span></span>
      <Badge v-if="visionGated" kind="warn" :title="visionGatedWhy">视觉已关</Badge>
      <span>vEgo <span class="sl-tabular">{{ vEgoKmh }} km/h</span></span>
      <span>路沿余量 <span class="sl-tabular">{{ edgeClearance }}</span></span>
      <span
        class="text-sl-text-3"
        title="本道边界线置信（laneLineProbs/Stds 过门）:该侧可信时目标按车道线相对判定,不可信时回退路径相对"
      >
        车道线
        <Badge :kind="laneLeftValid ? 'accent' : 'muted'">L</Badge>
        <Badge :kind="laneRightValid ? 'accent' : 'muted'">R</Badge>
      </span>
      <span
        title="每侧横向预算:BSM 报警为 0,侧向目标按车身间隙折算;避让偏置 ≤ 偏置侧预算"
      >预算 <span class="sl-tabular">L {{ fmtBudget(budgetLeft) }}</span> · <span class="sl-tabular">R {{ fmtBudget(budgetRight) }}</span></span>
      <span
        title="目标道变道清空（eagled 时间投影:近区/速度未知/投影冲突即拦,远而快的侧车放行）"
      >变道
        <Badge v-if="changeClearLeft !== undefined" :kind="changeClearLeft ? 'accent' : 'warn'">L {{ changeClearLeft ? "通" : "拦" }}</Badge>
        <Badge v-if="changeClearRight !== undefined" :kind="changeClearRight ? 'accent' : 'warn'">R {{ changeClearRight ? "通" : "拦" }}</Badge>
      </span>
    </div>

    <!-- 视觉被标定门关掉时解释目标=0：否则「一直是 0」看起来像坏了 -->
    <div
      v-if="visionGated"
      class="mt-1 rounded-md bg-sl-warn/10 px-3 py-2 text-[12px] text-sl-warn"
    >
      {{ visionGatedWhy }}
    </div>

    <!-- 标定/启用入口已移到 设置 → 转向 → 横向避让:未完成在线标定时开关
         在那里置灰,进度与原因同屏可见,不再两头找 -->
    <div v-if="avStale" class="mt-2 text-[12px] leading-relaxed text-sl-text-3">
      eagled 未运行——先在 设置 → 转向 → 横向避让 完成相机在线标定并开启
      避让（行驶中自动收敛，无需手动操作），这里才有目标数据。
      标定进度与安装偏移精修也都在那一栏。
    </div>
  </section>
</template>
