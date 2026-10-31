<script setup lang="ts">
/** SVG 柱状图（电池页的电压 / 内阻两张图共用）：固定 12 根左右的短柱，
 *  纵轴 lo～hi 三条刻度线，每根柱顶标数值，底部标序号；marks 在柱顶再标一行小字（最高 / 最低）。 */
import { computed } from "vue";
import { formatNumber } from "@/lib/toyota";

type Tone = "accent" | "warn" | "danger" | "info";

const props = defineProps<{
  title: string;
  subtitle: string;
  values: number[];
  /** 与 values 等长：每根柱的颜色 */
  tones: Tone[];
  /** 与 values 等长：柱顶小字，空串不标；有标注的柱加描边 */
  marks: string[];
  lo: number;
  hi: number;
  digits: number;
}>();

const W = 360;
const H = 190;
const LEFT = 30;
const BOTTOM = 24;
const TOP = 16;

const barWidth = computed(() => (W - LEFT - 6) / props.values.length);
const y = (v: number) => TOP + (H - TOP - BOTTOM) * (1 - (v - props.lo) / (props.hi - props.lo));
const ticks = computed(() => [props.lo, (props.lo + props.hi) / 2, props.hi]);
const bars = computed(() =>
  props.values.map((v, i) => {
    const x = LEFT + i * barWidth.value + 3;
    const top = y(Math.max(props.lo, v));
    return { i, x, top, width: barWidth.value - 6, center: x + (barWidth.value - 6) / 2, value: v };
  }),
);
</script>

<template>
  <section class="sl-card px-2 pb-2 pt-3">
    <h3 class="mx-2 text-[15px] font-semibold text-sl-text-1">{{ title }}</h3>
    <p class="mx-2 mb-1.5 text-[12px] text-sl-text-2">{{ subtitle }}</p>
    <svg :viewBox="`0 0 ${W} ${H}`" role="img" :aria-label="title" class="block h-auto w-full">
      <g v-for="t in ticks" :key="t">
        <line :x1="LEFT" :x2="W" :y1="y(t)" :y2="y(t)" stroke="var(--color-sl-border)" />
        <text :x="LEFT - 4" :y="y(t) + 3" font-size="8.5" text-anchor="end" fill="var(--color-sl-text-3)" font-family="monospace">{{ formatNumber(t, digits) }}</text>
      </g>
      <g v-for="b in bars" :key="b.i">
        <rect
          :x="b.x" :y="b.top" :width="b.width" :height="H - BOTTOM - b.top" rx="3"
          :fill="`var(--color-sl-${tones[b.i]})`"
          :stroke="marks[b.i] ? 'var(--color-sl-text-1)' : undefined" :stroke-width="marks[b.i] ? 1.5 : undefined"
        />
        <text :x="b.center" :y="b.top - 4" font-size="8.5" text-anchor="middle" fill="var(--color-sl-text-1)" font-family="monospace">{{ formatNumber(b.value, digits) }}</text>
        <text :x="b.center" :y="H - 8" font-size="9" text-anchor="middle" fill="var(--color-sl-text-2)">{{ b.i + 1 }}</text>
        <text v-if="marks[b.i]" :x="b.center" :y="b.top - 14" font-size="8" text-anchor="middle" fill="var(--color-sl-warn)">{{ marks[b.i] }}</text>
      </g>
    </svg>
  </section>
</template>
