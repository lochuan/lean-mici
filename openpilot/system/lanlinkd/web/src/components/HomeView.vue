<script setup lang="ts">
/** 首页。复刻 sunnylink Home：设备卡片 + 面板磁贴网格（实测 248×248 三列）。 */
import {
  Boxes,
  Car,
  Code2,
  Cog,
  Eye,
  Gauge,
  Monitor,
  Package,
  Settings2,
  SlidersHorizontal,
} from "lucide-vue-next";
import DeviceCard from "./DeviceCard.vue";
import { panels } from "@/lib/store";
import type { Panel } from "@/lib/schema";

const emit = defineEmits<{ navigate: [string] }>();

const ICONS: Record<string, unknown> = {
  steering_wheel: Car,
  cruise_control: Gauge,
  display: Monitor,
  visuals: Eye,
  toggles: SlidersHorizontal,
  device: Cog,
  software: Package,
  developer: Code2,
  models: Boxes,
};

const iconFor = (p: Panel) => ICONS[p.icon ?? ""] ?? Settings2;

const countOf = (p: Panel) =>
  p.sections.reduce(
    (n, s) => n + (s.items?.length ?? 0) + (s.sub_panels ?? []).reduce((m, d) => m + d.items.length, 0),
    0,
  );
</script>

<template>
  <div class="mx-auto flex w-full max-w-[768px] flex-col gap-5 px-6 py-6">
    <DeviceCard />

    <div>
      <h2 class="mb-2.5 text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
        设备设置
      </h2>
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <button
          v-for="p in panels"
          :key="p.id"
          type="button"
          class="sl-card group flex flex-col items-start gap-2 p-4 text-left transition-colors hover:bg-sl-surface-2"
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
          <div class="min-w-0">
            <div class="truncate text-sm font-medium text-sl-text-1">{{ p.label }}</div>
            <div class="text-[11px] text-sl-text-3">{{ countOf(p) }} 项设置</div>
          </div>
        </button>
      </div>
    </div>
  </div>
</template>
