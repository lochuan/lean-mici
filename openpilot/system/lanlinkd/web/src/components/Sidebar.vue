<script setup lang="ts">
/** 侧边栏内容。复刻 sunnylink 的结构（实测 bg #181818），分组与图标来自 lib/nav.ts，与首页同源。
 *
 * 只负责内容；外壳由 App 决定：>=md 常驻 aside，<md 放进 reka Dialog 做 drawer
 * （关闭时整棵树卸载，Tab 不会落到屏外按钮上）。
 */
import { Activity, Home, RefreshCw } from "lucide-vue-next";
import { cn } from "@/lib/utils";
import { iconFor, navGroups } from "@/lib/nav";

const props = defineProps<{ current: string }>();
const emit = defineEmits<{ navigate: [string]; refresh: [] }>();
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="sl-hairline flex h-16 shrink-0 items-center gap-2.5 px-5">
      <div class="grid size-7 place-items-center rounded-md bg-sl-accent/15 ring-1 ring-inset ring-sl-accent/30">
        <Activity class="size-4 text-sl-accent" />
      </div>
      <div class="min-w-0">
        <div class="truncate text-sm font-semibold tracking-tight text-sl-text-1">LANLink</div>
        <div class="truncate text-[12px] text-sl-text-3">局域网设置</div>
      </div>
    </div>

    <nav class="flex-1 overflow-y-auto px-3 py-3" aria-label="主导航">
      <!-- 首页（对应 sunnylink 的 SUNNYLINK / Home 分组） -->
      <button
        type="button"
        :aria-current="props.current === '' ? 'page' : undefined"
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

      <div v-for="g in navGroups" :key="g.id">
        <div class="px-3 pb-1.5 pt-4 text-[12px] font-semibold tracking-wide text-sl-text-3">
          {{ g.label }}
        </div>

        <div class="flex flex-col gap-0.5">
          <button
            v-for="p in g.panels"
            :key="p.id"
            type="button"
            :aria-current="props.current === p.id ? 'page' : undefined"
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
      </div>
    </nav>

    <div class="flex items-center gap-1 border-t border-sl-border p-3">
      <button
        type="button"
        class="flex h-10 flex-1 items-center gap-2 rounded-lg px-3 text-[13px] text-sl-text-2 transition-colors hover:bg-sl-surface-2 hover:text-sl-text-1"
        @click="emit('refresh')"
      >
        <RefreshCw class="size-3.5" />
        刷新
      </button>
    </div>
  </div>
</template>
