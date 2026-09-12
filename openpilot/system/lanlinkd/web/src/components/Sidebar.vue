<script setup lang="ts">
/** 侧边栏。复刻 sunnylink 的结构（实测 bg #181818），图标用 lucide 对应上游语义。
 *
 * 响应式：>=md 常驻（与旧版一致）；<md 变 overlay drawer（手机上
 * 248px 常驻栏会把内容挤没），由父组件经 open 控制滑入滑出。
 */
import { computed } from "vue";
import {
  Activity,
  Bluetooth,
  Boxes,
  Car,
  Code2,
  Cog,
  Eye,
  Gauge,
  Home,
  Monitor,
  Package,
  Settings2,
  SlidersHorizontal,
} from "lucide-vue-next";
import { cn } from "@/lib/utils";
import { panels } from "@/lib/store";
import type { Panel } from "@/lib/schema";

const props = defineProps<{ current: string; open?: boolean }>();
const emit = defineEmits<{ navigate: [string] }>();

/** schema 的 icon 名 → lucide 组件 */
const ICONS: Record<string, unknown> = {
  steering_wheel: Car,
  cruise_control: Gauge,
  display: Monitor,
  visuals: Eye,
  toggles: SlidersHorizontal,
  device: Cog,
  bluetooth: Bluetooth,
  software: Package,
  developer: Code2,
  models: Boxes,
  vehicle: Car,
  status: Activity,
};

const iconFor = (p: Panel) => ICONS[p.icon ?? ""] ?? Settings2;
const items = computed(() => panels.value);
</script>

<template>
  <aside
    :class="
      cn(
        'fixed inset-y-0 left-0 z-40 flex w-[248px] shrink-0 flex-col border-r border-sl-border bg-sl-surface transition-transform duration-200',
        '-translate-x-full md:static md:translate-x-0',
        props.open && 'translate-x-0',
      )
    "
  >
    <div class="sl-hairline flex h-16 shrink-0 items-center gap-2.5 px-5">
      <div class="grid size-7 place-items-center rounded-md bg-sl-accent/15 ring-1 ring-inset ring-sl-accent/30">
        <Activity class="size-4 text-sl-accent" />
      </div>
      <div class="min-w-0">
        <div class="truncate text-sm font-semibold tracking-tight text-sl-text-1">LANLink</div>
        <div class="truncate text-[11px] text-sl-text-3">局域网设置</div>
      </div>
    </div>

    <nav class="flex-1 overflow-y-auto px-3 py-3">
      <!-- 首页（对应 sunnylink 的 SUNNYLINK / Home 分组） -->
      <button
        type="button"
        :class="
          cn(
            'flex h-10 w-full items-center gap-2.5 rounded-lg px-3 text-left text-sm transition-colors',
            props.current === ''
              ? 'bg-sl-accent/12 font-medium text-sl-text-1 ring-1 ring-inset ring-sl-accent/25'
              : 'text-sl-text-2 hover:bg-sl-surface-2 hover:text-sl-text-1',
          )
        "
        @click="emit('navigate', '')"
      >
        <Home :class="cn('size-4 shrink-0', props.current === '' ? 'text-sl-accent' : 'text-sl-text-3')" />
        <span class="truncate">首页</span>
      </button>

      <div class="px-3 pb-1.5 pt-4 text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
        设备设置
      </div>

      <div class="flex flex-col gap-0.5">
        <button
          v-for="p in items"
          :key="p.id"
          type="button"
          :class="
            cn(
              'flex h-10 items-center gap-2.5 rounded-lg px-3 text-left text-sm transition-colors',
              props.current === p.id
                ? 'bg-sl-surface-3 font-medium text-sl-text-1 ring-1 ring-inset ring-sl-border'
                : 'text-sl-text-2 hover:bg-sl-surface-2 hover:text-sl-text-1',
            )
          "
          @click="emit('navigate', p.id)"
        >
          <component
            :is="iconFor(p)"
            :class="cn('size-4 shrink-0', props.current === p.id ? 'text-sl-accent' : 'text-sl-text-3')"
          />
          <span class="truncate">{{ p.label }}</span>
        </button>
      </div>
    </nav>

    <slot name="footer" />
  </aside>
</template>
