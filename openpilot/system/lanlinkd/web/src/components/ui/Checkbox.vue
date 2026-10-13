<script setup lang="ts">
/** 复选框（shadcn-vue Checkbox 形态）。 */
import { CheckboxIndicator, CheckboxRoot } from "reka-ui";
import { Check } from "lucide-vue-next";

defineProps<{ disabled?: boolean; ariaLabel?: string }>();
const model = defineModel<boolean>({ default: false });
</script>

<template>
  <CheckboxRoot
    v-model="model"
    :disabled="disabled"
    :aria-label="ariaLabel"
    class="grid size-5 shrink-0 place-items-center rounded-md bg-sl-surface ring-1 ring-inset ring-sl-border-strong transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=checked]:bg-sl-accent data-[state=checked]:text-sl-accent-text data-[disabled]:opacity-40"
  >
    <CheckboxIndicator>
      <Check class="size-3.5" />
    </CheckboxIndicator>
  </CheckboxRoot>
</template>
