<script setup lang="ts">
/** 诊断卡片外壳：标题 + 状态徽章 + 一句结论 + 原因 / 建议 + （数据不足时的）进度 + 图 + 折叠的详细数据。
 *  四张卡片的骨架一致，图和额外内容经默认插槽传入。 */
import Accordion from "../ui/Accordion.vue";
import Badge from "../ui/Badge.vue";
import type { Progress, Verdict } from "@/lib/diagnostics";

export interface DetailRow {
  label: string;
  value: string;
}

defineProps<{
  title: string;
  verdict: Verdict;
  progress?: Progress | null;
  details: DetailRow[];
}>();
</script>

<template>
  <section class="sl-card px-5 py-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">{{ title }}</h2>
      <Badge :kind="verdict.tone">{{ verdict.badge }}</Badge>
    </div>
    <div class="mt-2 text-[17px] font-semibold leading-snug text-sl-text-1">{{ verdict.headline }}</div>

    <p v-if="verdict.advice" class="mt-1.5 text-[13px] leading-relaxed text-sl-text-2">{{ verdict.advice }}</p>

    <div v-if="progress" class="mt-3">
      <div class="h-1.5 overflow-hidden rounded-sm bg-sl-surface-3">
        <div class="h-full bg-sl-info transition-[width] duration-700" :style="{ width: `${progress.pct}%` }" />
      </div>
      <div class="mt-1 text-[12px] text-sl-text-3">{{ progress.text }}</div>
    </div>

    <slot />


    <slot name="after" />

    <div class="mt-2">
      <Accordion :items="[{ id: 'details', label: '详细数据' }]">
        <dl class="sl-tabular grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 py-1 text-[12px]">
          <template v-for="row in details" :key="row.label">
            <dt class="text-sl-text-3">{{ row.label }}</dt>
            <dd class="text-right text-sl-text-1">{{ row.value }}</dd>
          </template>
        </dl>
      </Accordion>
    </div>
  </section>
</template>
