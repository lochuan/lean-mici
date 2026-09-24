<script setup lang="ts">
/** 下拉选择（schema 里 `option` + options[] 的渲染形态）。 */
import { computed } from "vue";
import {
  SelectContent,
  SelectItem,
  SelectItemText,
  SelectPortal,
  SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectViewport,
} from "reka-ui";
import { Check, ChevronDown } from "lucide-vue-next";
import { cn } from "@/lib/utils";
import type { OptionChoice } from "@/lib/schema";

const props = defineProps<{
  modelValue: string;
  options: OptionChoice[];
  disabled?: boolean;
  pending?: boolean;
  ariaLabel?: string;
  /** 逐项禁用及原因，由调用方按规则算好 */
  optionState?: Record<string, { disabled: boolean; reason: string }>;
}>();

const emit = defineEmits<{ commit: [string] }>();

const label = computed(
  () => props.options.find((o) => String(o.value) === props.modelValue)?.label ?? "--",
);

const stateOf = (v: string) => props.optionState?.[v] ?? { disabled: false, reason: "" };
</script>

<template>
  <SelectRoot
    :model-value="props.modelValue"
    :disabled="props.disabled || props.pending"
    @update:model-value="emit('commit', String($event))"
  >
    <SelectTrigger
      :aria-label="props.ariaLabel"
      :class="
        cn(
          'inline-flex h-[34px] items-center justify-between gap-1.5 rounded-lg',
          'bg-sl-surface px-2.5 text-[13px] text-sl-text-1 ring-1 ring-inset ring-sl-border',
          'transition-colors hover:bg-sl-surface-3',
          'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        )
      "
    >
      <SelectValue :placeholder="label">{{ label }}</SelectValue>
      <ChevronDown class="size-4 shrink-0 text-sl-text-3" />
    </SelectTrigger>

    <SelectPortal>
      <SelectContent
        position="popper"
        :side-offset="6"
        class="z-50 max-h-[min(360px,60vh)] min-w-[160px] overflow-hidden rounded-xl border border-sl-border bg-sl-surface-2 shadow-2xl shadow-black/60 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
      >
        <SelectViewport class="p-1">
          <SelectItem
            v-for="opt in props.options"
            :key="String(opt.value)"
            :value="String(opt.value)"
            :disabled="stateOf(String(opt.value)).disabled"
            :title="stateOf(String(opt.value)).reason"
            :class="
              cn(
                'relative flex cursor-pointer select-none items-center justify-between gap-3 rounded-lg',
                'px-3 py-2 text-sm text-sl-text-1 outline-none',
                'data-[highlighted]:bg-sl-surface-3 data-[state=checked]:text-sl-accent',
                'data-[disabled]:pointer-events-none data-[disabled]:opacity-35',
              )
            "
          >
            <SelectItemText>{{ opt.label }}</SelectItemText>
            <Check v-if="String(opt.value) === props.modelValue" class="size-4 shrink-0" />
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>
