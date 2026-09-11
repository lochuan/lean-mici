<script setup lang="ts">
/** 开关。尺寸取自 sunnylink 实测：44×26，圆头 22。
 *  用 reka-ui 的 Switch 拿到可访问性与键盘交互（空格/回车）。 */
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
        'relative inline-flex h-[26px] w-[44px] shrink-0 cursor-pointer items-center rounded-full',
        'border border-transparent transition-colors duration-200 ease-out',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sl-accent',
        props.modelValue ? 'bg-sl-accent' : 'bg-sl-surface-3',
        (props.disabled || props.pending) && 'cursor-not-allowed opacity-40',
      )
    "
    @update:model-value="emit('update:modelValue', $event)"
  >
    <SwitchThumb
      :class="
        cn(
          'pointer-events-none block h-[22px] w-[22px] rounded-full bg-white shadow-sm',
          'transition-transform duration-200 ease-out will-change-transform',
          props.modelValue ? 'translate-x-[20px]' : 'translate-x-[2px]',
          props.pending && 'animate-pulse',
        )
      "
    />
  </SwitchRoot>
</template>
