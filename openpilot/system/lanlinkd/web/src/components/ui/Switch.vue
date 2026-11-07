<script setup lang="ts">
/** 开关（shadcn-vue Switch 形态）。
 *  reka-ui SwitchRoot/Thumb 提供可访问性与键盘交互；类名对齐 shadcn-vue
 *  官方规范（data-state 驱动 + 语义 token），尺寸沿用 44×26。
 */
import { SwitchRoot, SwitchThumb } from "reka-ui";
import { cn } from "@/lib/utils";

const props = defineProps<{
  modelValue: boolean;
  disabled?: boolean;
  /** 写入进行中：保持可见但不接受新输入，避免连点产生竞态 */
  pending?: boolean;
  ariaLabel?: string;
}>();

const emit = defineEmits<{ "update:modelValue": [boolean] }>();
</script>

<template>
  <SwitchRoot
    :model-value="props.modelValue"
    :disabled="props.disabled || props.pending"
    :aria-label="props.ariaLabel"
    :class="
      cn(
        'peer inline-flex h-[26px] w-[44px] shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-sm transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        'data-[state=checked]:bg-primary data-[state=unchecked]:bg-input',
        (props.disabled || props.pending) && 'cursor-not-allowed opacity-50',
      )
    "
    @update:model-value="emit('update:modelValue', $event)"
  >
    <SwitchThumb
      :class="
        cn(
          'pointer-events-none block size-[22px] rounded-full bg-background shadow-lg ring-0',
          'transition-transform duration-200 ease-out will-change-transform',
          props.modelValue ? 'translate-x-[18px]' : 'translate-x-0',
          props.pending && 'animate-pulse',
        )
      "
    />
  </SwitchRoot>
</template>
