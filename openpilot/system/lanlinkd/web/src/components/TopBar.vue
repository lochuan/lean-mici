<script setup lang="ts">
/** 顶栏。
 *
 * 与 sunnylink 保持一致（2026-09-12 实地核对 /dashboard）：顶栏只放
 * **设备状态胶囊**（"Connecting..." / "Always Offroad" 这类状态词）+ 搜索，
 * 版本信息以 VERSION / BRANCH / COMMIT 三段呈现在设备卡片里。
 *
 * sunnylink 全站**没有** CPU 温度、车速一类的遥测读数——它是设置工具，
 * 不是仪表盘。所以这里只显示状态，不显示传感器数值。
 *
 * 注意 /api/status 的轮询照常进行：它同时带回 paramsVersion，是察觉
 * **车机端**改动的唯一途径（见 store.pollStatus）。
 */
import { computed } from "vue";
import { Search } from "lucide-vue-next";
import { store } from "@/lib/store";
import { cn } from "@/lib/utils";

const emit = defineEmits<{ search: [] }>();

const s = computed(() => store.status);

/** 状态胶囊：复刻 sunnylink 的 "Device status: X" 按钮 */
const state = computed(() => {
  if (!store.connected) return { text: "连接中断", tone: "danger" as const };
  if (s.value?.stale) return { text: "数据过期", tone: "warn" as const };
  if (store.params.AlwaysOffroad === "1") return { text: "始终驻车", tone: "warn" as const };
  if (s.value?.system?.ignition) return { text: "已通电", tone: "accent" as const };
  return { text: "已熄火", tone: "muted" as const };
});

const TONE = {
  accent: "bg-sl-accent/10 text-sl-accent ring-sl-accent/25",
  warn: "bg-sl-warn/10 text-sl-warn ring-sl-warn/25",
  danger: "bg-sl-danger/10 text-sl-danger ring-sl-danger/25",
  muted: "bg-sl-surface-3 text-sl-text-2 ring-sl-border",
};
</script>

<template>
  <div class="sl-hairline flex h-16 shrink-0 items-center gap-4 bg-sl-bg/80 px-6 backdrop-blur">
    <!-- 搜索：sunnylink 把它放在顶栏中部 -->
    <button
      type="button"
      class="flex h-9 min-w-0 max-w-[448px] flex-1 items-center gap-2 rounded-lg bg-sl-surface-2 px-3 text-left ring-1 ring-inset ring-sl-border transition-colors hover:ring-sl-border-strong"
      aria-label="搜索设置（⌘K）"
      @click="emit('search')"
    >
      <Search class="size-3.5 shrink-0 text-sl-text-3" />
      <span class="flex-1 truncate text-[13px] text-sl-text-3">搜索设置…</span>
      <kbd class="shrink-0 rounded border border-sl-border bg-sl-surface-3 px-1.5 py-0.5 text-[10px] text-sl-text-3">
        ⌘K
      </kbd>
    </button>

    <!-- 设备状态胶囊（圆角全满，对应实测 border-radius 极大值） -->
    <span
      :class="
        cn(
          'shrink-0 rounded-full px-3 py-1.5 text-[12px] font-medium ring-1 ring-inset',
          TONE[state.tone],
        )
      "
    >
      {{ state.text }}
    </span>
  </div>
</template>
