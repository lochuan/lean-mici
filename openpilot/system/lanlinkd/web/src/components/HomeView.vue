<script setup lang="ts">
/** 首页。复刻 sunnylink Home：设备卡片 + 面板磁贴网格；分组、图标与侧边栏同源（lib/nav.ts）。 */
import DeviceCard from "./DeviceCard.vue";
import { iconFor, navGroups, panelSummary } from "@/lib/nav";

const emit = defineEmits<{ navigate: [string] }>();
</script>

<template>
  <div class="mx-auto flex w-full max-w-[768px] flex-col gap-5 px-4 py-4 md:px-6 md:py-6">
    <DeviceCard />

    <section v-for="g in navGroups" :key="g.id">
      <h2 class="mb-2.5 text-[13px] font-semibold tracking-wide text-sl-text-3">
        {{ g.label }}
      </h2>
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <button
          v-for="p in g.panels"
          :key="p.id"
          type="button"
          class="sl-card sl-press group flex min-w-0 flex-col items-start gap-2 p-4 text-left hover:bg-sl-surface-2"
          @click="emit('navigate', p.id)"
        >
          <div
            class="grid size-10 place-items-center rounded-lg bg-sl-surface-3 ring-1 ring-inset ring-sl-border transition-colors group-hover:bg-sl-accent/12 group-hover:ring-sl-accent/30"
          >
            <component
              :is="iconFor(p)"
              class="size-[18px] text-sl-text-2 transition-colors group-hover:text-sl-accent"
            />
          </div>
          <div class="min-w-0 max-w-full">
            <div class="truncate text-sm font-medium text-sl-text-1">{{ p.label }}</div>
            <div class="mt-0.5 line-clamp-2 text-[12px] leading-snug text-sl-text-3">{{ panelSummary(p) }}</div>
          </div>
        </button>
      </div>
    </section>
  </div>
</template>
