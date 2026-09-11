<script setup lang="ts">
/** 分段按钮组（schema 的 `multiple_button`）。
 *
 * 每个选项可以单独禁用并给出原因（真实用例：MadsSteeringMode 在 rivian /
 * 无 vehicle bus 的 tesla 上只剩「退出」可选）。被禁的按钮保留在原位并置灰，
 * 这样用户能看到"有这个模式但我的车不支持"，而不是选项凭空少了几个。
 */
import { cn } from "@/lib/utils";
import type { OptionChoice } from "@/lib/schema";

const props = defineProps<{
  modelValue: string;
  options: OptionChoice[];
  disabled?: boolean;
  pending?: boolean;
  optionState?: Record<string, { disabled: boolean; reason: string }>;
}>();

const emit = defineEmits<{ commit: [string] }>();

const stateOf = (v: string) => props.optionState?.[v] ?? { disabled: false, reason: "" };

function pick(opt: OptionChoice): void {
  const v = String(opt.value);
  if (props.disabled || props.pending || stateOf(v).disabled || v === props.modelValue) return;
  emit("commit", v);
}
</script>

<template>
  <div
    role="radiogroup"
    :class="
      cn(
        'inline-flex flex-wrap items-center gap-1 rounded-lg bg-sl-bg p-1 ring-1 ring-inset ring-sl-border',
        props.disabled && 'opacity-40',
      )
    "
  >
    <button
      v-for="opt in props.options"
      :key="String(opt.value)"
      type="button"
      role="radio"
      :aria-checked="String(opt.value) === props.modelValue"
      :disabled="props.disabled || props.pending || stateOf(String(opt.value)).disabled"
      :title="stateOf(String(opt.value)).reason"
      :class="
        cn(
          'h-9 shrink-0 rounded-md px-3 text-[13px] font-medium transition-colors',
          'disabled:pointer-events-none disabled:opacity-35',
          String(opt.value) === props.modelValue
            ? 'bg-sl-accent text-sl-accent-text shadow-sm'
            : 'text-sl-text-2 hover:bg-sl-surface-3 hover:text-sl-text-1',
          props.pending && String(opt.value) === props.modelValue && 'animate-pulse',
        )
      "
      @click="pick(opt)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>
