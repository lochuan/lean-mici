<script setup lang="ts">
/** 按钮。三种视觉：accent（主操作）、surface（默认）、ghost（次要）。 */
import { cn } from "@/lib/utils";

const props = withDefaults(
  defineProps<{
    variant?: "accent" | "surface" | "ghost" | "danger";
    size?: "sm" | "md" | "lg";
    disabled?: boolean;
    title?: string;
  }>(),
  { variant: "surface", size: "md" },
);

const VARIANTS = {
  accent: "bg-sl-accent text-sl-accent-text hover:bg-sl-accent-hover font-semibold",
  surface: "bg-sl-surface-3 text-sl-text-1 hover:bg-sl-border ring-1 ring-inset ring-sl-border",
  ghost: "bg-transparent text-sl-text-2 hover:bg-sl-surface-2 hover:text-sl-text-1",
  danger: "bg-sl-danger/12 text-sl-danger ring-1 ring-inset ring-sl-danger/30 hover:bg-sl-danger/20",
} as const;

const SIZES = {
  sm: "h-8 px-3 text-[13px] rounded-md gap-1.5",
  // 车机是触摸屏，主按钮不能小于 40px 高，否则很难点准
  md: "h-10 px-4 text-sm rounded-lg gap-2",
  lg: "h-12 px-5 text-base rounded-lg gap-2",
} as const;
</script>

<template>
  <button
    type="button"
    :disabled="props.disabled"
    :title="props.title"
    :class="
      cn(
        'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap',
        'transition-colors duration-150 active:scale-[0.98]',
        'disabled:pointer-events-none disabled:opacity-40',
        VARIANTS[props.variant],
        SIZES[props.size],
      )
    "
  >
    <slot />
  </button>
</template>
