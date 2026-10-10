<script setup lang="ts">
/** 转向能力 / EPS：半圆仪表（弯道里转向力不够的时间占比）+ EPS 罢工次数 + 转向力使用分布。 */
import { computed } from "vue";
import type { SteeringDiag } from "@/lib/schema";
import { fmtDuration, steeringView, toneColor, STEER_SATURATION_BAD, STEER_SATURATION_OK } from "@/lib/diagnostics";
import DiagCard from "./DiagCard.vue";

const props = defineProps<{ steering: SteeringDiag }>();

const view = computed(() => steeringView(props.steering));

// ---- 半圆仪表：刻度 0–50%（更高按满格画），色区与判定阈值一致 ----
const CX = 100;
const CY = 100;
const R = 72;
const ZONE_R = 88;
const SCALE_MAX_PCT = 50;
const ZONES = [
  { from: 0, to: STEER_SATURATION_OK * 100, color: "var(--color-sl-accent)" },
  { from: STEER_SATURATION_OK * 100, to: STEER_SATURATION_BAD * 100, color: "var(--color-sl-warn)" },
  { from: STEER_SATURATION_BAD * 100, to: SCALE_MAX_PCT, color: "var(--color-sl-danger)" },
];

function arc(fromPct: number, toPct: number, r: number): string {
  const pt = (pct: number) => {
    const a = Math.PI * (1 - Math.min(1, pct / SCALE_MAX_PCT));
    return `${(CX + r * Math.cos(a)).toFixed(2)} ${(CY - r * Math.sin(a)).toFixed(2)}`;
  };
  return `M ${pt(fromPct)} A ${r} ${r} 0 0 1 ${pt(toPct)}`;
}

const valueArc = computed(() => {
  const pct = view.value.saturatedPct;
  return pct === null || pct <= 0 ? null : arc(0, Math.max(pct, 0.6), R);
});
const gaugeColor = computed(() => toneColor(view.value.tone));
const epsColor = computed(() => toneColor(view.value.eps.tone));

const CONTROL_TEXT: Record<SteeringDiag["control"], string> = { torque: "扭矩控制", angle: "角度控制", "": "—" };

const details = computed(() => {
  const s = props.steering;
  return [
    { label: "横向控制方式", value: CONTROL_TEXT[s.control] },
    { label: "辅助驾驶激活时长", value: fmtDuration(s.activeSeconds) },
    { label: "其中弯道", value: fmtDuration(s.curveSeconds) },
    { label: "弯道里转向力不够", value: fmtDuration(s.saturatedCurveSeconds) },
    { label: "最大横向加速度", value: s.maxLatAccel === null ? "—" : `${s.maxLatAccel.toFixed(2)} m/s²` },
    { label: "EPS 临时故障", value: `${s.epsTempFaults} 次` },
    { label: "EPS 永久故障", value: s.epsPermanent ? "有" : "无" },
  ];
});
</script>

<template>
  <DiagCard title="转向能力" :verdict="view" :progress="view.progress" :details="details">
    <div class="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div class="flex flex-col items-center">
        <svg viewBox="0 0 200 118" class="w-full max-w-[240px]" role="img" aria-label="弯道里转向力不够的时间占比">
          <path v-for="z in ZONES" :key="z.from" :d="arc(z.from, z.to, ZONE_R)" fill="none"
                :stroke="z.color" stroke-opacity="0.45" stroke-width="4" />
          <path :d="arc(0, SCALE_MAX_PCT, R)" fill="none" stroke="var(--color-sl-surface-3)" stroke-width="14" />
          <path v-if="valueArc" :d="valueArc" fill="none" :stroke="gaugeColor" stroke-width="14" />
          <text :x="CX" :y="CY - 8" text-anchor="middle" font-size="30" font-weight="600"
                :fill="view.saturatedPct === null ? 'var(--color-sl-text-3)' : gaugeColor">
            {{ view.saturatedPct === null ? "—" : `${view.saturatedPct}%` }}
          </text>
          <g fill="var(--color-sl-text-3)" font-size="10" text-anchor="middle">
            <text :x="CX - R" :y="CY + 16">0</text>
            <text :x="CX + R" :y="CY + 16">50%+</text>
          </g>
        </svg>
        <div class="text-center text-[12px] text-sl-text-3">弯道里转向力不够的时间占比</div>
      </div>

      <div class="flex flex-col justify-center rounded-lg bg-sl-surface-2 px-4 py-3 ring-1 ring-inset ring-sl-border">
        <div class="flex items-baseline gap-2">
          <span class="sl-tabular text-[30px] font-semibold leading-tight" :style="{ color: epsColor }">{{ view.eps.count }}</span>
          <span class="text-[13px] text-sl-text-2">次 EPS 罢工</span>
        </div>
        <div class="text-[12px]" :style="{ color: epsColor }">{{ view.eps.text }}</div>
        <p class="mt-1.5 text-[12px] leading-relaxed text-sl-text-3">
          Toyota 的转向助力（EPS）在转得太快、太猛时会临时拒绝执行，仪表会报“转向暂时不可用”，几秒后自动恢复。
        </p>
      </div>
    </div>

    <template #after>
      <div v-if="view.usage" class="mt-4 border-t border-sl-border pt-3">
        <div class="mb-2 text-[12px] font-semibold text-sl-text-2">转向力使用分布（占可用力的比例）</div>
        <div class="flex flex-col gap-1.5">
          <div v-for="(b, i) in view.usage" :key="b.label" class="sl-tabular flex items-center gap-2 text-[11px]">
            <span class="w-[4.5em] shrink-0 text-sl-text-3">{{ b.label }}</span>
            <div class="h-2.5 flex-1 overflow-hidden rounded-sm bg-sl-surface-3">
              <div class="h-full transition-[width] duration-700" :class="i === 4 ? 'bg-sl-warn' : 'bg-sl-info'" :style="{ width: `${b.pct}%` }" />
            </div>
            <span class="w-[3em] shrink-0 text-right text-sl-text-2">{{ b.pct }}%</span>
          </div>
        </div>
        <div class="mt-1.5 text-[11px] text-sl-text-3">60% 以上两档占比越高，说明转向越吃力</div>
      </div>
    </template>
  </DiagCard>
</template>
