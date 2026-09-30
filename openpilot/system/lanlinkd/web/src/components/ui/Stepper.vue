<script setup lang="ts">
/** 数值步进器（schema 里 `option` + min/max/step 的渲染形态）。
 *
 * sunnylink 用 ± 按钮 + 数值显示（实测 40×40 按钮）；这里加了一条进度轨道，
 * 让当前值在范围中的位置一眼可见。
 *
 * 写入做了防抖：每次 put 在设备上都是 params.put(block=True) 落盘，
 * 按住 ± 连点会打出几十次磁盘写。本地值即时响应，静止 350ms 后才提交。
 */
import { computed, ref, watch } from "vue";
import { Minus, Plus } from "lucide-vue-next";
import { cn, formatSliderValue } from "@/lib/utils";
import { nextValue } from "@/lib/numeric";

const props = defineProps<{
  modelValue: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  disabled?: boolean;
  pending?: boolean;
}>();

const emit = defineEmits<{ commit: [number] }>();

const step = computed(() => props.step ?? 1);
const local = ref(props.modelValue);
let timer: ReturnType<typeof setTimeout> | undefined;

// 外部值变化（设备端改动 / 写入失败回滚）时同步本地值，
// 但正在编辑（有待提交的定时器）时不要打断用户
watch(
  () => props.modelValue,
  (v) => {
    if (timer === undefined) local.value = v;
  },
);

/** 按 step 对齐并夹在 [min,max]，同时消掉浮点误差（见 lib/numeric.ts） */
function schedule(v: number): void {
  local.value = v;
  if (timer !== undefined) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = undefined;
    if (local.value !== props.modelValue) emit("commit", local.value);
  }, 350);
}

function nudge(dir: 1 | -1): void {
  if (props.disabled) return;
  const next = nextValue(local.value, dir, props.min, props.max, step.value);
  if (next !== local.value) schedule(next);
}

const atMin = computed(() => local.value <= props.min);
const atMax = computed(() => local.value >= props.max);

const pct = computed(() => {
  const span = props.max - props.min;
  if (span <= 0) return 0;
  return ((local.value - props.min) / span) * 100;
});

const display = computed(() => formatSliderValue(local.value, props.step));
const btn =
  "grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-sl-surface-3 ring-1 ring-inset " +
  "ring-sl-border text-sl-text-1 transition-colors hover:bg-sl-border " +
  "disabled:pointer-events-none disabled:opacity-30 active:scale-95";
</script>

<template>
  <div class="flex flex-col items-stretch gap-2">
    <div class="flex items-center gap-2">
      <button
        type="button"
        :class="btn"
        :disabled="props.disabled || atMin"
        aria-label="减少"
        @click="nudge(-1)"
      >
        <Minus class="size-4" />
      </button>

      <div
        :class="
          cn(
            'min-w-[104px] rounded-lg bg-sl-bg px-3 py-2 text-center ring-1 ring-inset ring-sl-border',
            props.disabled && 'opacity-40',
          )
        "
      >
        <span class="sl-tabular text-[15px] font-semibold text-sl-text-1">{{ display }}</span>
        <span v-if="props.unit" class="ml-1 text-xs text-sl-text-3">{{ props.unit }}</span>
      </div>

      <button
        type="button"
        :class="btn"
        :disabled="props.disabled || atMax"
        aria-label="增加"
        @click="nudge(1)"
      >
        <Plus class="size-4" />
      </button>
    </div>

    <!-- 轨道：纯指示，交互仍走 ± 按钮（触摸屏上拖拽细轨道很难对准） -->
    <div class="h-1 overflow-hidden rounded-full bg-sl-surface-3" :class="props.disabled && 'opacity-40'">
      <div
        class="h-full rounded-full bg-sl-accent transition-[width] duration-200 ease-out"
        :class="props.pending && 'animate-pulse'"
        :style="{ width: `${pct}%` }"
      />
    </div>
  </div>
</template>
