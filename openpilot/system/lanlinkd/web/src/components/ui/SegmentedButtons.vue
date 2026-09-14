<script setup lang="ts">
/** 分段按钮组（schema 的 `multiple_button`）——shadcn-vue ToggleGroup 形态。
 *
 * 内部用 reka-ui 的 ToggleGroup 原语（role=radiogroup、roving focus、
 * 键盘方向键），对外 API 保持 modelValue/options/commit 不变。
 * 每个选项可以单独禁用并给出原因（真实用例：MadsSteeringMode 在 rivian /
 * 无 vehicle bus 的 tesla 上只剩「退出」可选）。被禁的按钮保留在原位并置灰，
 * 这样用户能看到"有这个模式但我的车不支持"，而不是选项凭空少了几个。
 */
import { ToggleGroupItem, ToggleGroupRoot } from "reka-ui";
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

function onPick(v: unknown): void {
  const value = String(v ?? "");
  if (!value || props.disabled || props.pending || stateOf(value).disabled || value === props.modelValue) return;
  emit("commit", value);
}
</script>

<template>
  <ToggleGroupRoot
    type="single"
    :model-value="props.modelValue"
    :disabled="props.disabled || props.pending"
    :class="
      cn(
        'inline-flex flex-wrap items-center gap-1 rounded-lg bg-sl-bg p-1 ring-1 ring-inset ring-sl-border',
        (props.disabled || props.pending) && 'opacity-40',
      )
    "
    @update:model-value="onPick"
  >
    <ToggleGroupItem
      v-for="opt in props.options"
      :key="String(opt.value)"
      :value="String(opt.value)"
      :disabled="props.disabled || props.pending || stateOf(String(opt.value)).disabled"
      :title="stateOf(String(opt.value)).reason"
      :class="
        cn(
          'inline-flex h-9 shrink-0 items-center justify-center rounded-md px-3 text-[13px] font-medium transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          'data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:shadow-sm',
          'data-[state=off]:text-muted-foreground hover:data-[state=off]:bg-sl-surface-3 hover:data-[state=off]:text-sl-text-1',
          'disabled:pointer-events-none disabled:opacity-35',
          props.pending && String(opt.value) === props.modelValue && 'animate-pulse',
        )
      "
    >
      {{ opt.label }}
    </ToggleGroupItem>
  </ToggleGroupRoot>
</template>
