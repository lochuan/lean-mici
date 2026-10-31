<script setup lang="ts">
/** 盲区监测 BSM：识别结果 + 本次行驶左右两侧的触发次数；行驶中当前有车的一侧高亮。
 *  只能靠触发次数证明 BSM 在工作：车机里关掉 BSM、启动时漏识别都看不出来，由结论文案提示。 */
import { computed } from "vue";
import type { BsmDiag } from "@/lib/schema";
import { bsmView, fmtDuration } from "@/lib/diagnostics";
import DiagCard from "./DiagCard.vue";

// 停车后 leftOccupied/rightOccupied 是上次行驶最后一帧，不再高亮
const props = defineProps<{ bsm: BsmDiag; driving: boolean }>();

const view = computed(() => bsmView(props.bsm));

const sides = computed(() => [
  { label: "左侧", count: props.bsm.leftEvents, occupied: props.driving && props.bsm.leftOccupied },
  { label: "右侧", count: props.bsm.rightEvents, occupied: props.driving && props.bsm.rightOccupied },
]);

const details = computed(() => {
  const b = props.bsm;
  return [
    { label: "识别结果", value: b.available === null ? "—" : b.available ? "有" : "无" },
    { label: "左侧触发", value: `${b.leftEvents} 次` },
    { label: "右侧触发", value: `${b.rightEvents} 次` },
    { label: "计入时长（16 km/h 以上）", value: fmtDuration(b.watchSeconds) },
  ];
});
</script>

<template>
  <DiagCard title="盲区监测 BSM" :verdict="view" :progress="view.progress" :details="details">
    <div v-if="bsm.available" class="mt-3 grid grid-cols-2 gap-3">
      <div v-for="s in sides" :key="s.label"
           class="rounded-lg bg-sl-surface-2 px-4 py-3 ring-1 ring-inset transition-colors"
           :class="s.occupied ? 'ring-sl-warn' : 'ring-sl-border'">
        <div class="flex items-center justify-between text-[12px]">
          <span class="text-sl-text-2">{{ s.label }}</span>
          <span v-if="s.occupied" class="font-semibold text-sl-warn">当前有车</span>
        </div>
        <div class="mt-1 flex items-baseline gap-1.5">
          <span class="sl-tabular text-[30px] font-semibold leading-tight text-sl-text-1">{{ s.count }}</span>
          <span class="text-[13px] text-sl-text-2">次触发</span>
        </div>
      </div>
    </div>
  </DiagCard>
</template>
