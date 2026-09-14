<script setup lang="ts">
/** 手风琴（shadcn-vue Accordion 形态）：子设置原地展开，不遮挡页面。
 *
 * items 定义触发器行；每项内容经 #item 具名插槽分发。
 * 前置条件不满足的项置灰 + Lock + title 说明（与原抽屉入口行为一致）。
 */
import {
  AccordionContent,
  AccordionHeader,
  AccordionItem,
  AccordionRoot,
  AccordionTrigger,
} from "reka-ui";
import { ChevronDown, Lock } from "lucide-vue-next";

export interface AccordionEntry {
  id: string;
  label: string;
  count?: number;
  disabled?: boolean;
  disabledReason?: string;
}

defineProps<{
  items: AccordionEntry[];
}>();
</script>

<template>
  <AccordionRoot type="multiple" class="mt-2 flex flex-col gap-2">
    <AccordionItem
      v-for="it in items"
      :key="it.id"
      :value="it.id"
      :disabled="it.disabled"
      :title="it.disabled ? it.disabledReason : undefined"
      class="overflow-hidden rounded-lg bg-sl-surface-2 ring-1 ring-inset ring-sl-border transition-colors data-[state=open]:bg-sl-surface-3/40 data-[disabled]:opacity-40"
    >
      <AccordionHeader class="flex">
        <AccordionTrigger
          class="group flex h-12 w-full flex-1 items-center justify-between px-4 text-left transition-colors hover:bg-sl-surface-3"
        >
          <span class="flex items-center gap-2 text-sm text-sl-text-1">
            <Lock v-if="it.disabled" class="size-3.5 text-sl-text-3" />
            {{ it.label }}
            <span v-if="it.count !== undefined" class="text-xs text-sl-text-3">{{ it.count }} 项</span>
          </span>
          <ChevronDown
            class="size-4 text-sl-text-3 transition-transform duration-200 group-data-[state=open]:rotate-180"
          />
        </AccordionTrigger>
      </AccordionHeader>
      <AccordionContent
        class="overflow-hidden border-t border-sl-border/70 data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down"
      >
        <div class="px-4 py-2">
          <slot :item="it" />
        </div>
      </AccordionContent>
    </AccordionItem>
  </AccordionRoot>
</template>
