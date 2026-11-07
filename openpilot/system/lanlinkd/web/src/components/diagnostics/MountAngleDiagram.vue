<script setup lang="ts">
/** 相机安装角度示意图（单轴）：俯视图看左右（yaw），侧视图看上下（pitch）。
 *  扇形 = 允许 / 理想范围，虚线 = 车头正前方，彩色线段 + 圆点 = 设备当前朝向，弧线箭头 = 该往哪边转（图上唯一的箭头）。
 *  角度按“允许范围边界画成 35°”放大，标题里的度数是真实值。 */
import { computed } from "vue";
import { CAMERA_GOOD, type MountAngle } from "@/lib/diagnostics";

const props = defineProps<{ kind: "top" | "side"; angle: MountAngle; color: string }>();

/** 俯视车头朝上（屏幕 −90°），侧视车头朝右（0°）；SVG 的 y 向下，角度顺时针为正 */
const LAYOUT = {
  top: {
    w: 180, h: 160, pivotX: 90, pivotY: 100, rayR: 88, forwardDeg: -90, title: "俯视 · 左右朝向",
    directionLabels: [{ text: "左", x: 35, y: 57 }, { text: "右", x: 145, y: 57 }],
  },
  side: {
    w: 180, h: 150, pivotX: 66, pivotY: 58, rayR: 100, forwardDeg: 0, title: "侧视 · 上下朝向",
    directionLabels: [{ text: "上", x: 160, y: 12 }, { text: "下", x: 172, y: 132 }],
  },
} as const;
const LIMIT_DRAWN_DEG = 35;
const OVERSHOOT = 1.15;

const layout = computed(() => LAYOUT[props.kind]);

const diagram = computed(() => {
  const { pivotX, pivotY, rayR, forwardDeg } = layout.value;
  const { deg, min, max, needsTurn } = props.angle;
  const drawScale = LIMIT_DRAWN_DEG / Math.max(-min, max);
  const toScreenDeg = (realDeg: number) => forwardDeg + realDeg * drawScale;
  const polarPoint = (screenDeg: number, r: number) => {
    const rad = (screenDeg * Math.PI) / 180;
    return { x: pivotX + r * Math.cos(rad), y: pivotY + r * Math.sin(rad) };
  };
  const sector = (from: number, to: number) => {
    const a = polarPoint(toScreenDeg(from), rayR);
    const b = polarPoint(toScreenDeg(to), rayR);
    return `M ${pivotX} ${pivotY} L ${a.x} ${a.y} A ${rayR} ${rayR} 0 0 1 ${b.x} ${b.y} Z`;
  };

  const clampedDeg = Math.max(OVERSHOOT * min, Math.min(OVERSHOOT * max, deg));
  const current = polarPoint(toScreenDeg(clampedDeg), rayR * 0.92);
  const forward = polarPoint(forwardDeg, rayR);

  // 当前朝向 → 正前方的弧线；该轴需要调整时带箭头
  const arcR = rayR * 0.55;
  const drawnSpan = clampedDeg * drawScale;
  const arcFrom = polarPoint(toScreenDeg(clampedDeg), arcR);
  const arcTo = polarPoint(forwardDeg, arcR);
  const arc = Math.abs(drawnSpan) >= 3
    ? `M ${arcFrom.x} ${arcFrom.y} A ${arcR} ${arcR} 0 0 ${drawnSpan > 0 ? 0 : 1} ${arcTo.x} ${arcTo.y}`
    : null;

  return {
    allowed: sector(min, max),
    ideal: sector(CAMERA_GOOD * min, CAMERA_GOOD * max),
    forward, current, arc, needsTurn,
  };
});

const markerId = computed(() => `diag-mount-arrow-${props.kind}`);
</script>

<template>
  <figure class="flex min-w-0 flex-col items-center gap-1">
    <figcaption class="text-[12px] font-medium text-sl-text-2">
      {{ layout.title }}<span class="ml-1.5 font-semibold" :style="{ color }">{{ angle.facing }}</span>
    </figcaption>
    <svg :viewBox="`0 0 ${layout.w} ${layout.h}`" class="w-full max-w-56" role="img" :aria-label="`${layout.title}示意图`">
      <defs>
        <marker :id="markerId" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" :fill="color" />
        </marker>
      </defs>

      <!-- 车身简笔 -->
      <g fill="var(--color-sl-surface-2)" stroke="var(--color-sl-border-strong)" stroke-width="1.5">
        <template v-if="kind === 'top'">
          <rect x="62" y="62" width="56" height="110" rx="14" />
          <path d="M66 96 Q90 86 114 96" fill="none" />
        </template>
        <template v-else>
          <path d="M10 125 L10 95 Q12 60 40 52 L62 50 L100 88 L150 95 Q160 98 160 112 L160 125 Z" />
          <circle cx="40" cy="126" r="12" />
          <circle cx="135" cy="126" r="12" />
        </template>
      </g>

      <!-- 允许 / 理想范围 -->
      <path :d="diagram.allowed" fill="var(--color-sl-text-3)" fill-opacity="0.08" stroke="var(--color-sl-border-strong)" />
      <path :d="diagram.ideal" fill="var(--color-sl-accent)" fill-opacity="0.15" stroke="var(--color-sl-accent)" stroke-opacity="0.5" />

      <!-- 车头正前方 -->
      <line :x1="layout.pivotX" :y1="layout.pivotY" :x2="diagram.forward.x" :y2="diagram.forward.y"
            stroke="var(--color-sl-text-3)" stroke-dasharray="3 3" />

      <!-- 该往哪边转 -->
      <path v-if="diagram.arc" :d="diagram.arc" fill="none" :stroke="color" stroke-width="1.5"
            :marker-end="diagram.needsTurn ? `url(#${markerId})` : undefined" />

      <!-- 设备当前朝向 -->
      <line :x1="layout.pivotX" :y1="layout.pivotY" :x2="diagram.current.x" :y2="diagram.current.y"
            :stroke="color" stroke-width="2.5" />
      <circle :cx="diagram.current.x" :cy="diagram.current.y" r="4" :fill="color" />
      <rect :x="layout.pivotX - 5" :y="layout.pivotY - 3" width="10" height="6" rx="1.5"
            fill="var(--color-sl-text-1)" />

      <g fill="var(--color-sl-text-3)" font-size="11" text-anchor="middle" dominant-baseline="middle">
        <text v-for="label in layout.directionLabels" :key="label.text" :x="label.x" :y="label.y">{{ label.text }}</text>
      </g>
    </svg>
  </figure>
</template>
