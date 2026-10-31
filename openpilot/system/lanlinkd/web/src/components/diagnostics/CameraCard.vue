<script setup lang="ts">
/** 相机安装角度：俯视 / 侧视示意图（扇形 = 允许 / 理想范围，箭头 = 当前朝向）+ 调整建议，
 *  下方“量一量”只计算建议的相机偏移，不写入参数。 */
import { computed, ref } from "vue";
import type { CameraDiag } from "@/lib/schema";
import { cameraView, suggestCameraOffset, toneColor } from "@/lib/diagnostics";
import DiagCard from "./DiagCard.vue";
import MountAngleDiagram from "./MountAngleDiagram.vue";

const props = defineProps<{ camera: CameraDiag | null }>();

const view = computed(() => cameraView(props.camera));
const progress = computed(() =>
  view.value.progress === null ? null : { pct: view.value.progress, text: "标定完成后显示安装角度" },
);

const verdictColor = computed(() => toneColor(view.value.tone));

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
    <div v-if="view.diagrams" class="mt-3 flex flex-col gap-3">
      <div class="grid grid-cols-2 gap-3">
        <MountAngleDiagram kind="top" :angle="view.diagrams.top" :color="verdictColor" />
        <MountAngleDiagram kind="side" :angle="view.diagrams.side" :color="verdictColor" />
      </div>

      <div v-if="view.adjust" class="flex flex-col gap-0.5">
        <div class="text-[15px] font-semibold" :style="{ color: verdictColor }">{{ view.adjust }}</div>
        <div class="text-[12px] text-sl-text-2">原地转动设备，不要平移；{{ view.aimShift }}</div>
      </div>

      <div class="flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-sl-text-2">
        <div class="flex items-center gap-2">
          <span class="inline-block h-0.5 w-4 rounded-full" :style="{ background: verdictColor }" />设备当前朝向
        </div>
        <div class="flex items-center gap-2">
          <span class="inline-block size-3 rounded-sm bg-sl-accent/15 ring-1 ring-inset ring-sl-accent/50" />理想范围
        </div>
        <div class="flex items-center gap-2">
          <span class="inline-block size-3 rounded-sm bg-sl-surface-2 ring-1 ring-inset ring-sl-border-strong" />允许范围（超出会标定失败）
        </div>
        <div class="text-sl-text-3">角度已放大示意，度数为实际值</div>
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
