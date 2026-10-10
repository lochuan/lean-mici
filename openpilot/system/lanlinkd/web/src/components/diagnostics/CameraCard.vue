<script setup lang="ts">
/** 相机安装角度：瞄准框（外框 = 允许范围，绿框 = 理想范围，点 = 当前朝向）+ 调整建议，
 *  下方“量一量”只计算建议的相机偏移，不写入参数。 */
import { computed, ref } from "vue";
import type { CameraDiag } from "@/lib/schema";
import { cameraView, suggestCameraOffset, toneColor, CAMERA_GOOD } from "@/lib/diagnostics";
import DiagCard from "./DiagCard.vue";

const props = defineProps<{ camera: CameraDiag | null }>();

const view = computed(() => cameraView(props.camera));
const progress = computed(() =>
  view.value.progress === null ? null : { pct: view.value.progress, text: "标定完成后显示安装角度" },
);

// ---- 瞄准框几何（按度数等比例；y 向下为正，与 pitch>0 朝下一致）----
const MARGIN_X = 30;
const MARGIN_Y = 22;
const MAX_W = 240;
const MAX_H = 156;

const box = computed(() => {
  const cam = props.camera;
  if (!cam || !view.value.aim) return null;
  const { yawDeg: yawLim, pitchUpDeg: up, pitchDownDeg: down } = cam.limits;
  const s = Math.min(MAX_W / (2 * yawLim), MAX_H / (up + down));
  const w = 2 * yawLim * s;
  const h = (up + down) * s;
  const cx = MARGIN_X + w / 2;
  const cy = MARGIN_Y + up * s;
  // 超出允许范围的点画在框外一点点，避免跑出画布
  const ax = Math.max(-1.15 * yawLim, Math.min(1.15 * yawLim, view.value.aim.x));
  const ay = Math.max(-1.15 * up, Math.min(1.15 * down, view.value.aim.y));
  const px = cx + ax * s;
  const py = cy + ay * s;
  const len = Math.hypot(cx - px, cy - py);
  const arrow = view.value.adjust && len > 16
    ? { x1: px, y1: py, x2: px + (cx - px) * (1 - 9 / len), y2: py + (cy - py) * (1 - 9 / len) }
    : null;
  return {
    vbW: w + 2 * MARGIN_X, vbH: h + 2 * MARGIN_Y,
    x: MARGIN_X, y: MARGIN_Y, w, h, cx, cy,
    ideal: { x: cx - CAMERA_GOOD * yawLim * s, y: cy - CAMERA_GOOD * up * s, w: 2 * CAMERA_GOOD * yawLim * s, h: CAMERA_GOOD * (up + down) * s },
    px, py, arrow,
  };
});

const dotColor = computed(() => toneColor(view.value.tone));

const details = computed(() => {
  const cam = props.camera;
  if (!cam) return [{ label: "相机标定", value: "无数据" }];
  return [
    { label: "数据来源", value: cam.source === "live" ? "实时" : "上次行驶缓存" },
    { label: "标定状态", value: `${cam.calStatus}（${Math.round(cam.calPerc)}%）` },
    { label: "左右（yaw，>0 朝左）", value: `${cam.yawDeg.toFixed(2)}° / 限值 ±${cam.limits.yawDeg.toFixed(2)}°` },
    {
      label: "上下（pitch，>0 朝下）",
      value: `${cam.pitchDeg.toFixed(2)}° / 限值 上 ${cam.limits.pitchUpDeg.toFixed(1)}° 下 ${cam.limits.pitchDownDeg.toFixed(1)}°`,
    },
    { label: "当前相机偏移", value: fmtOffset(cam.cameraOffsetM) },
  ];
});

// ---- 量一量 ----
// type=number 的 v-model 会自动转成数字，清空时是 ""
const leftCm = ref<string | number>("");
const rightCm = ref<string | number>("");

function parseCm(v: string | number): number | null {
  if (v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const suggestion = computed(() => suggestCameraOffset(parseCm(leftCm.value), parseCm(rightCm.value)));
const currentOffset = computed(() => props.camera?.cameraOffsetM ?? null);
const matchesCurrent = computed(
  () => suggestion.value !== null && currentOffset.value !== null
    && Math.abs(suggestion.value.offsetM - currentOffset.value) < 0.005,
);

function fmtOffset(m: number): string {
  return `${m > 0 ? "+" : ""}${m.toFixed(2)} m`;
}
</script>

<template>
  <DiagCard title="相机安装" :verdict="view" :progress="progress" :details="details">
    <div v-if="box" class="mt-3 flex flex-col items-center gap-3 sm:flex-row sm:items-center sm:gap-6">
      <svg
        :viewBox="`0 0 ${box.vbW} ${box.vbH}`"
        class="h-52 w-auto max-w-full shrink-0"
        role="img"
        aria-label="相机朝向瞄准框"
      >
        <defs>
          <marker id="diag-aim-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
            <path d="M0 0 L10 5 L0 10 z" :fill="dotColor" />
          </marker>
        </defs>
        <!-- 允许范围 -->
        <rect :x="box.x" :y="box.y" :width="box.w" :height="box.h" rx="6"
              fill="var(--color-sl-surface-2)" stroke="var(--color-sl-border-strong)" stroke-width="1.5" />
        <!-- 理想范围（50% 限值） -->
        <rect :x="box.ideal.x" :y="box.ideal.y" :width="box.ideal.w" :height="box.ideal.h" rx="4"
              fill="var(--color-sl-accent)" fill-opacity="0.12" stroke="var(--color-sl-accent)" stroke-opacity="0.5" />
        <!-- 十字准线 -->
        <line :x1="box.x" :y1="box.cy" :x2="box.x + box.w" :y2="box.cy" stroke="var(--color-sl-border-strong)" stroke-dasharray="3 3" />
        <line :x1="box.cx" :y1="box.y" :x2="box.cx" :y2="box.y + box.h" stroke="var(--color-sl-border-strong)" stroke-dasharray="3 3" />
        <circle :cx="box.cx" :cy="box.cy" r="2.5" fill="var(--color-sl-text-3)" />
        <!-- 方向标注 -->
        <g fill="var(--color-sl-text-3)" font-size="11" text-anchor="middle" dominant-baseline="middle">
          <text :x="box.x - 13" :y="box.cy">左</text>
          <text :x="box.x + box.w + 13" :y="box.cy">右</text>
          <text :x="box.cx" :y="box.y - 11">上</text>
          <text :x="box.cx" :y="box.y + box.h + 11">下</text>
        </g>
        <!-- 当前朝向 → 中心 -->
        <line v-if="box.arrow" :x1="box.arrow.x1" :y1="box.arrow.y1" :x2="box.arrow.x2" :y2="box.arrow.y2"
              :stroke="dotColor" stroke-width="2" marker-end="url(#diag-aim-arrow)" />
        <circle :cx="box.px" :cy="box.py" r="6" :fill="dotColor" stroke="var(--color-sl-surface)" stroke-width="2" />
      </svg>

      <div class="flex flex-col gap-1.5 text-[12px] text-sl-text-2">
        <div v-if="view.adjust" class="text-[15px] font-semibold" :style="{ color: dotColor }">{{ view.adjust }}</div>
        <div class="flex items-center gap-2">
          <span class="inline-block size-3 rounded-full" :style="{ background: dotColor }" />设备当前朝向
        </div>
        <div class="flex items-center gap-2">
          <span class="inline-block size-3 rounded-sm bg-sl-accent/15 ring-1 ring-inset ring-sl-accent/50" />理想范围
        </div>
        <div class="flex items-center gap-2">
          <span class="inline-block size-3 rounded-sm bg-sl-surface-2 ring-1 ring-inset ring-sl-border-strong" />允许范围（超出会标定失败）
        </div>
      </div>
    </div>

    <template #after>
      <div class="mt-4 border-t border-sl-border pt-3">
        <div class="text-[13px] font-semibold text-sl-text-1">量一量：设备装在中线上了吗？</div>
        <p class="mt-1 text-[12px] leading-relaxed text-sl-text-3">
          用尺子量设备镜头到前挡风玻璃左、右边缘的水平距离，自动算出“相机偏移”该填多少。
        </p>
        <div class="mt-3 grid grid-cols-2 gap-3">
          <label class="flex flex-col gap-1 text-[12px] text-sl-text-2">
            到左边缘（cm）
            <input v-model="leftCm" type="number" inputmode="decimal" min="0" placeholder="如 72"
                   class="h-10 w-full rounded-lg bg-sl-bg px-3 text-[15px] text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border placeholder:text-sl-text-3 focus:ring-sl-accent" />
          </label>
          <label class="flex flex-col gap-1 text-[12px] text-sl-text-2">
            到右边缘（cm）
            <input v-model="rightCm" type="number" inputmode="decimal" min="0" placeholder="如 80"
                   class="h-10 w-full rounded-lg bg-sl-bg px-3 text-[15px] text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border placeholder:text-sl-text-3 focus:ring-sl-accent" />
          </label>
        </div>

        <div v-if="suggestion" class="mt-3 rounded-lg bg-sl-surface-2 px-4 py-3 ring-1 ring-inset ring-sl-border">
          <div class="text-[12px] text-sl-text-3">{{ suggestion.side }}，建议相机偏移</div>
          <div class="sl-tabular mt-0.5 text-[22px] font-semibold text-sl-text-1">{{ fmtOffset(suggestion.offsetM) }}</div>
          <div v-if="currentOffset !== null" class="mt-1 text-[12px]">
            <span v-if="matchesCurrent" class="text-sl-accent">与当前设置一致，无需修改</span>
            <span v-else class="text-sl-warn">当前设置 {{ fmtOffset(currentOffset) }}，建议改为 {{ fmtOffset(suggestion.offsetM) }}</span>
          </div>
          <div v-if="suggestion.hint" class="mt-1 text-[12px] text-sl-danger">{{ suggestion.hint }}</div>
          <div class="mt-2 text-[12px] text-sl-text-3">到「视觉 → 相机 → 相机偏移」里设置（看不到这一项时，先到「开发者」里打开「显示高级选项」）</div>
        </div>
      </div>
    </template>
  </DiagCard>
</template>
