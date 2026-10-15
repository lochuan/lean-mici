<script setup lang="ts">
/** 跑偏：俯视车道图（车按平均位置平移，虚线 = 模型规划位置，按 3.6 m 车道等比例）
 *  + 结论 + 可能原因检查清单。 */
import { computed } from "vue";
import { CircleCheck, CircleQuestionMark, CircleX, TriangleAlert } from "lucide-vue-next";
import type { DriftDiag } from "@/lib/schema";
import { driftChecklist, driftView, fmtDuration, toneColor, torqueOffsetTiltDeg, type CheckState } from "@/lib/diagnostics";
import DiagCard from "./DiagCard.vue";

const props = defineProps<{ drift: DriftDiag }>();

const view = computed(() => driftView(props.drift));
const checklist = computed(() => driftChecklist(props.drift));

// ---- 俯视图几何：车道 3.6 m 宽，横向 1 m = 50 px；车头朝上 ----
const PX_PER_M = 50;
const CENTER_X = 150;
const LANE_HALF = 1.8 * PX_PER_M;
const CAR_W = 1.8 * PX_PER_M;
const CAR_H = 120;
const CAR_TOP = 22;

const clampM = (m: number) => Math.max(-1.2, Math.min(1.2, m));
const hasData = computed(() => view.value.cause !== null);
const carX = computed(() => CENTER_X + (hasData.value ? clampM(props.drift.carOffsetM ?? 0) : 0) * PX_PER_M);
const planX = computed(() =>
  hasData.value && props.drift.planOffsetM !== null ? CENTER_X + clampM(props.drift.planOffsetM) * PX_PER_M : null,
);
const carColor = computed(() => toneColor(view.value.tone));
const offsetCm = computed(() => Math.round(Math.abs(props.drift.carOffsetM ?? 0) * 100));
const showOffsetArrow = computed(() => hasData.value && Math.abs(carX.value - CENTER_X) >= 4);

const ICONS: Record<CheckState, { icon: unknown; cls: string }> = {
  ok: { icon: CircleCheck, cls: "text-sl-accent" },
  warn: { icon: TriangleAlert, cls: "text-sl-warn" },
  bad: { icon: CircleX, cls: "text-sl-danger" },
  unknown: { icon: CircleQuestionMark, cls: "text-sl-text-3" },
};

function signedCm(m: number | null): string {
  if (m === null) return "—";
  const v = Math.round(m * 100);
  return v === 0 ? "0 cm" : `${v > 0 ? "偏右" : "偏左"} ${Math.abs(v)} cm`;
}

const ACCURATE_TEXT: Record<DriftDiag["accurateAngle"], string> = {
  ready: "已就绪", pending: "未就绪", unknown: "未知", "n/a": "不适用",
};

const details = computed(() => {
  const d = props.drift;
  return [
    { label: "有效直道样本", value: fmtDuration(d.seconds) },
    { label: "车在车道内平均位置", value: signedCm(d.carOffsetM) },
    { label: "模型规划平均位置", value: signedCm(d.planOffsetM) },
    {
      label: "方向盘零点",
      value: d.angleOffsetDeg === null ? "—"
        : `${d.angleOffsetDeg.toFixed(2)}°${d.angleOffsetValid === false ? "（无效）" : ""}`,
    },
    { label: "Toyota 高精度转角", value: ACCURATE_TEXT[d.accurateAngle] },
    {
      label: "扭矩偏置",
      value: d.latAccelOffset === null ? "—"
        : `${d.latAccelOffset.toFixed(3)} m/s²（≈ ${Math.abs(torqueOffsetTiltDeg(d.latAccelOffset)).toFixed(1)}°）`,
    },
  ];
});
</script>

<template>
  <DiagCard title="跑偏" :verdict="view" :progress="view.progress" :details="details">
    <div class="mt-3 flex justify-center">
      <svg viewBox="0 0 300 180" class="w-full max-w-[340px]" role="img" aria-label="车在车道内的平均位置">
        <defs>
          <marker id="diag-drift-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" :fill="carColor" />
          </marker>
        </defs>
        <!-- 路面与车道线 -->
        <rect :x="CENTER_X - LANE_HALF - 24" y="0" :width="2 * LANE_HALF + 48" height="180" fill="var(--color-sl-surface-2)" />
        <line :x1="CENTER_X - LANE_HALF" y1="0" :x2="CENTER_X - LANE_HALF" y2="180" stroke="var(--color-sl-text-2)" stroke-width="3" />
        <line :x1="CENTER_X + LANE_HALF" y1="0" :x2="CENTER_X + LANE_HALF" y2="180" stroke="var(--color-sl-text-2)" stroke-width="3" stroke-dasharray="18 12" />
        <!-- 车道中心 -->
        <line :x1="CENTER_X" y1="0" :x2="CENTER_X" y2="180" stroke="var(--color-sl-lane-center)" stroke-width="3" stroke-dasharray="10 6" />

        <!-- 简化车形（车头朝上） -->
        <g :opacity="hasData ? 1 : 0.35" :transform="`translate(${carX - CENTER_X} 0)`">
          <rect :x="CENTER_X - CAR_W / 2 - 4" :y="CAR_TOP + 16" width="6" height="22" rx="2" fill="var(--color-sl-text-3)" />
          <rect :x="CENTER_X + CAR_W / 2 - 2" :y="CAR_TOP + 16" width="6" height="22" rx="2" fill="var(--color-sl-text-3)" />
          <rect :x="CENTER_X - CAR_W / 2 - 4" :y="CAR_TOP + CAR_H - 38" width="6" height="22" rx="2" fill="var(--color-sl-text-3)" />
          <rect :x="CENTER_X + CAR_W / 2 - 2" :y="CAR_TOP + CAR_H - 38" width="6" height="22" rx="2" fill="var(--color-sl-text-3)" />
          <rect :x="CENTER_X - CAR_W / 2" :y="CAR_TOP" :width="CAR_W" :height="CAR_H" rx="20"
                fill="var(--color-sl-surface-3)" :stroke="carColor" stroke-width="2" />
          <rect :x="CENTER_X - CAR_W / 2 + 10" :y="CAR_TOP + 26" :width="CAR_W - 20" height="22" rx="6"
                fill="var(--color-sl-bg)" opacity="0.8" />
          <line :x1="CENTER_X" :y1="CAR_TOP + 4" :x2="CENTER_X" :y2="CAR_TOP + CAR_H - 4" :stroke="carColor" stroke-opacity="0.5" stroke-dasharray="3 3" />
        </g>

        <!-- 模型规划位置 -->
        <line v-if="planX !== null" :x1="planX" y1="4" :x2="planX" y2="176"
              stroke="var(--color-sl-info)" stroke-width="2" stroke-dasharray="7 5" />

        <!-- 偏移量 -->
        <template v-if="showOffsetArrow">
          <line :x1="CENTER_X" y1="160" :x2="carX" y2="160" :stroke="carColor" stroke-width="2" marker-end="url(#diag-drift-arrow)" />
          <text :x="carX + (carX > CENTER_X ? 8 : -8)" y="164" :text-anchor="carX > CENTER_X ? 'start' : 'end'"
                font-size="12" font-weight="600" :fill="carColor">{{ offsetCm }} cm</text>
        </template>
      </svg>
    </div>
    <div class="mt-2 flex flex-wrap justify-center gap-x-4 gap-y-1 text-[11px] text-sl-text-3">
      <span class="flex items-center gap-1.5"><span class="inline-block h-3 border-l border-dotted border-sl-text-3" />车道中心</span>
      <span class="flex items-center gap-1.5"><span class="inline-block h-3 border-l-2 border-dashed border-sl-info" />模型规划位置</span>
      <span>车 = 本次平均位置</span>
    </div>

    <template #after>
      <div class="mt-4 border-t border-sl-border pt-3">
        <div class="mb-2 text-[12px] font-semibold text-sl-text-2">可能原因检查</div>
        <ul class="flex flex-col gap-2">
          <li v-for="c in checklist" :key="c.id" class="flex items-start gap-2.5 text-[13px]">
            <component :is="ICONS[c.state].icon" :class="['mt-0.5 size-4 shrink-0', ICONS[c.state].cls]" />
            <div class="min-w-0">
              <span class="text-sl-text-1">{{ c.label }}</span>
              <span class="text-sl-text-3"> · </span>
              <span class="text-sl-text-2">{{ c.text }}</span>
            </div>
          </li>
        </ul>
      </div>
    </template>
  </DiagCard>
</template>
