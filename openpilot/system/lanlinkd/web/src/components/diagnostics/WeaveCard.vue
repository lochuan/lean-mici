<script setup lang="ts">
/** 画龙 / 左右摆：最近 30 秒车道内横向位置折线 + 画龙时间占比。
 *  浅色带以平均位置为中心 ±15 cm（与后端画龙判定的 30 cm 峰峰值同口径）：
 *  车整体偏一侧属于“跑偏”，不在这里算；这里只看围绕自身位置的左右摆。 */
import { computed } from "vue";
import type { WeaveDiag } from "@/lib/schema";
import { fmtDuration, toneColor, traceSegments, traceYRange, weaveView } from "@/lib/diagnostics";
import DiagCard from "./DiagCard.vue";

// 停车后 trace 冻结在上次行驶的最后 30 秒，时间轴文案要跟着变
const props = defineProps<{ weave: WeaveDiag; driving: boolean }>();
const traceWindowText = computed(() => (props.driving ? "最近 30 秒" : "停车前 30 秒"));

const view = computed(() => weaveView(props.weave));
const statColor = computed(() => toneColor(view.value.tone));

// ---- 折线图几何：横轴 = 时间（左旧右新），纵轴 = 横向位置（偏右朝上）----
const W = 300;
const H = 100;
const NORMAL_BAND_M = 0.15;

const traceVals = computed(() => props.weave.trace.filter((v): v is number => v !== null));
const yRange = computed(() => traceYRange(traceVals.value, NORMAL_BAND_M));
const meanM = computed(() => yRange.value.meanM);
const yOf = (m: number) => ((yRange.value.hiM - m) / (yRange.value.hiM - yRange.value.loM)) * H;
const xOf = (i: number) => (i / Math.max(1, props.weave.trace.length - 1)) * W;

const segments = computed(() =>
  traceSegments(props.weave.trace).map((seg) => seg.map((p) => ({ x: xOf(p.i), y: yOf(p.v) }))),
);

const details = computed(() => {
  const w = props.weave;
  const sec = (v: number | null) => (v === null ? "—" : `${v.toFixed(2)} 秒`);
  return [
    { label: "直道样本", value: fmtDuration(w.seconds) },
    { label: "已评估 10 秒窗口", value: `${w.windows} 个（画龙 ${w.weaveWindows} 个）` },
    { label: "摆幅中位数（峰峰值）", value: w.medianP2pM === null ? "—" : `${Math.round(w.medianP2pM * 100)} cm` },
    { label: "周期中位数", value: sec(w.medianPeriodS) },
    { label: "横向延迟（学到）", value: sec(w.lateralDelayS) },
    { label: "横向延迟（出厂）", value: sec(w.factoryDelayS) },
  ];
});
</script>

<template>
  <DiagCard title="画龙 / 左右摆" :verdict="view" :progress="view.progress" :details="details">
    <div v-if="view.weavePct !== null" class="mt-3 flex flex-wrap items-end gap-x-6 gap-y-2">
      <div>
        <div class="text-[11px] text-sl-text-3">画龙时间占比</div>
        <div class="sl-tabular text-[30px] font-semibold leading-tight" :style="{ color: statColor }">{{ view.weavePct }}%</div>
      </div>
      <div v-if="view.amplitudeCm !== null || view.periodS !== null" class="pb-1 text-[13px] text-sl-text-2">
        <template v-if="view.amplitudeCm !== null">左右摆幅约 {{ view.amplitudeCm }} cm</template>
        <template v-if="view.amplitudeCm !== null && view.periodS !== null">、</template>
        <template v-if="view.periodS !== null">周期约 {{ view.periodS }} 秒</template>
      </div>
    </div>

    <!-- 绘图区横向拉伸（preserveAspectRatio=none，线宽不随缩放），文字标签用 HTML 叠加：手机和桌面字号一致 -->
    <div class="relative mt-3 h-[136px]">
      <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" class="absolute inset-x-0 top-3 bottom-3 h-[calc(100%-1.5rem)] w-full"
           role="img" :aria-label="`${traceWindowText}车道内横向位置`">
        <rect v-if="traceVals.length" x="0" :y="yOf(meanM + NORMAL_BAND_M)" :width="W"
              :height="yOf(meanM - NORMAL_BAND_M) - yOf(meanM + NORMAL_BAND_M)"
              fill="var(--color-sl-accent)" fill-opacity="0.12" />
        <line x1="0" :y1="yOf(0)" :x2="W" :y2="yOf(0)" stroke="var(--color-sl-lane-center)" stroke-width="2.5" stroke-dasharray="8 5"
              vector-effect="non-scaling-stroke" />
        <polyline v-for="(seg, k) in segments" :key="k"
                  :points="(seg.length > 1 ? seg : [seg[0], { x: seg[0].x + 0.5, y: seg[0].y }]).map((p) => `${p.x},${p.y}`).join(' ')"
                  fill="none" stroke="var(--color-sl-info)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"
                  vector-effect="non-scaling-stroke" />
      </svg>
      <span class="absolute left-1 -top-0.5 text-[10px] leading-none text-sl-text-3">偏右</span>
      <span class="absolute -bottom-0.5 left-1 text-[10px] leading-none text-sl-text-3">偏左</span>
      <div v-if="!segments.length" class="absolute inset-0 grid place-items-center px-6 text-center text-[12px] text-sl-text-3">
        {{ traceWindowText }}没有可用的直道数据
      </div>
    </div>
    <div class="mt-0.5 flex justify-between text-[10px] text-sl-text-3">
      <span>{{ driving ? "30 秒前" : "停车前 30 秒" }}</span>
      <span>{{ driving ? "现在" : "停车时" }}</span>
    </div>
    <div class="mt-1 text-[11px] text-sl-text-3">洋红色虚线 = 车道中心；浅绿色带 = 平均位置 ±15 cm 的正常晃动；线断开处是弯道、变道或车道线不清的时段</div>
  </DiagCard>
</template>
