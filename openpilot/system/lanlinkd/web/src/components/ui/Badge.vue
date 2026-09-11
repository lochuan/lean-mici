<script setup lang="ts">
/** 状态徽章。复刻 sunnylink 的 UNAVAILABLE / ADVANCED / REBOOT TO APPLY /
 * DEVICE ONLY 视觉语言（实测），用途是**解释为什么置灰**而不是隐藏控件。 */
import { computed } from "vue";
import { cn } from "@/lib/utils";

const props = withDefaults(
  defineProps<{ kind?: "muted" | "warn" | "danger" | "info" | "accent" }>(),
  { kind: "muted" },
);

const cls = computed(
  () =>
    ({
      muted: "bg-sl-surface-3 text-sl-text-3 ring-sl-border",
      warn: "bg-sl-warn/10 text-sl-warn ring-sl-warn/25",
      danger: "bg-sl-danger/10 text-sl-danger ring-sl-danger/25",
      info: "bg-sl-info/10 text-sl-info ring-sl-info/25",
      accent: "bg-sl-accent/10 text-sl-accent ring-sl-accent/25",
    })[props.kind],
);
</script>

<template>
  <span
    :class="
      cn(
        'inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1 ring-inset',
        cls,
      )
    "
  >
    <slot />
  </span>
</template>
