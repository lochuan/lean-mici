<script setup lang="ts">
/** 标签页（shadcn-vue Tabs 形态）：reka-ui 无头原语 + sl 主题。
 *
 * items 定义触发器；每页内容经 #[id] 具名插槽分发。内容用 force-mount + v-show 保留，
 * 切换标签不丢扫描结果；当前是否可见经插槽参数 active 告诉内容（用于暂停轮询）。
 */
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from "reka-ui";
import { cn } from "@/lib/utils";

export interface TabEntry {
  id: string;
  label: string;
}

defineProps<{ items: TabEntry[] }>();
const model = defineModel<string>({ required: true });
</script>

<template>
  <TabsRoot v-model="model" class="flex flex-col gap-3">
    <TabsList class="inline-flex flex-wrap items-center gap-1 self-start rounded-lg bg-sl-bg p-1 ring-1 ring-inset ring-sl-border">
      <TabsTrigger
        v-for="it in items"
        :key="it.id"
        :value="it.id"
        :class="
          cn(
            'inline-flex h-9 shrink-0 items-center justify-center rounded-md px-3 text-[13px] font-medium transition-colors',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            'data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-sm',
            'data-[state=inactive]:text-muted-foreground hover:data-[state=inactive]:bg-sl-surface-3 hover:data-[state=inactive]:text-sl-text-1',
          )
        "
      >
        {{ it.label }}
      </TabsTrigger>
    </TabsList>
    <TabsContent
      v-for="it in items"
      :key="it.id"
      :value="it.id"
      force-mount
      :class="model !== it.id && 'hidden'"
    >
      <slot :name="it.id" :active="model === it.id" />
    </TabsContent>
  </TabsRoot>
</template>
