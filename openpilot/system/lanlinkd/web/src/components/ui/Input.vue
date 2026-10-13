<script setup lang="ts">
/** 文本输入框（shadcn-vue Input 形态）。 */
defineProps<{ placeholder?: string; ariaLabel?: string; disabled?: boolean }>();
const model = defineModel<string>({ default: "" });
</script>

<template>
  <input
    v-model="model"
    type="text"
    :placeholder="placeholder"
    :aria-label="ariaLabel"
    :disabled="disabled"
    class="h-[34px] w-full rounded-lg bg-sl-surface px-2.5 text-[13px] text-sl-text-1 ring-1 ring-inset ring-sl-border placeholder:text-sl-text-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
  />
</template>
