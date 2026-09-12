<script setup lang="ts">
/** 顶栏。
 *
 * 状态胶囊复刻 sunnylink /dashboard 右上角的 "Device status" 按钮
 * （实测 2026-09-12）：状态词保持英文原词（Connecting... / Offroad /
 * Onroad / Always Offroad），点击展开设备详情弹层——里面有 Always
 * Offroad Mode 开关，对应 params.OffroadMode（upstream hardwared.py
 * 读它阻断 onroad；车机设置页的「强制离线模式」是同一个参数）。
 *
 * 版本信息仍以 VERSION / BRANCH / COMMIT 三段呈现在首页设备卡片里，
 * 弹层里带一份便于随时查看。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Search } from "lucide-vue-next";
import Switch from "./ui/Switch.vue";
import { store, writeParam, isPending } from "@/lib/store";
import { devicePill, networkLabel } from "@/lib/deviceState";
import { cn } from "@/lib/utils";

const emit = defineEmits<{ search: [] }>();

const open = ref(false);
const rootEl = ref<HTMLElement | null>(null);

const s = computed(() => store.status);

/** 状态胶囊（词表与优先级见 lib/deviceState.ts，有单测锁死） */
const pill = computed(() =>
  devicePill({
    connected: store.connected,
    stale: s.value?.stale,
    alwaysOffroad: store.params.OffroadMode === "1",
    started: s.value?.device?.started,
  }),
);

const TONE = {
  accent: "bg-sl-accent/10 text-sl-accent ring-sl-accent/25",
  warn: "bg-sl-warn/10 text-sl-warn ring-sl-warn/25",
  danger: "bg-sl-danger/10 text-sl-danger ring-sl-danger/25",
  muted: "bg-sl-surface-3 text-sl-text-2 ring-sl-border",
};

/** 胶囊按钮的完整 class（tone + 交互态），避免模板里拼三元 */
const pillClass = computed(() =>
  cn(
    "rounded-full px-3 py-1.5 text-[12px] font-medium ring-1 ring-inset transition-colors",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sl-accent",
    TONE[pill.value.tone],
    open.value ? "brightness-125" : "hover:brightness-110",
  ),
);

const alwaysOffroad = computed(() => store.params.OffroadMode === "1");

async function toggleAlwaysOffroad(v: boolean): Promise<void> {
  await writeParam("OffroadMode", v ? "1" : "0");
}

/** 弹层信息行：label（英文小标签，对齐首页设备卡片）+ 值 */
const rows = computed(() => [
  { label: "NETWORK", value: networkLabel(s.value?.device?.networkType) },
  { label: "VERSION", value: s.value?.system?.version },
  { label: "BRANCH", value: s.value?.system?.branch },
  { label: "COMMIT", value: s.value?.system?.commit?.slice(0, 12) },
]);

// ---- 弹层关闭：点外部 / Escape（SearchPalette 同款约定）----
function onDocMousedown(e: MouseEvent): void {
  if (open.value && rootEl.value && !rootEl.value.contains(e.target as Node)) {
    open.value = false;
  }
}
function onKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape") open.value = false;
}
onMounted(() => {
  document.addEventListener("mousedown", onDocMousedown);
  document.addEventListener("keydown", onKeydown);
});
onUnmounted(() => {
  document.removeEventListener("mousedown", onDocMousedown);
  document.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <div class="sl-hairline relative flex h-16 shrink-0 items-center gap-4 bg-sl-bg/80 px-6 backdrop-blur">
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

    <!-- 状态胶囊 = 按钮（复刻 sunnylink 的 "Device status: X"） -->
    <div ref="rootEl" class="relative shrink-0">
      <button
        type="button"
        :class="pillClass"
        :aria-expanded="open"
        aria-haspopup="dialog"
        :title="`Device status: ${pill.text}, click to view details`"
        @click="open = !open"
      >
        {{ pill.text }}
      </button>

      <!-- 设备详情弹层 -->
      <div
        v-if="open"
        role="dialog"
        aria-label="设备状态详情"
        class="absolute right-0 top-[calc(100%+10px)] z-50 w-[320px] rounded-xl border border-sl-border bg-sl-surface p-4 shadow-2xl shadow-black/50"
      >
        <!-- 状态行 -->
        <div class="flex items-center gap-2.5">
          <span
            :class="cn('size-2 rounded-full', pill.tone === 'accent' ? 'bg-sl-accent' : pill.tone === 'warn' ? 'bg-sl-warn' : pill.tone === 'danger' ? 'bg-sl-danger' : 'bg-sl-text-3')"
          />
          <span class="text-sm font-medium text-sl-text-1">{{ pill.text }}</span>
          <span class="ml-auto text-[11px] text-sl-text-3">Device status</span>
        </div>

        <!-- 版本/网络信息 -->
        <div class="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-sl-border pt-3">
          <div v-for="r in rows" :key="r.label" class="min-w-0">
            <div class="text-[10px] font-semibold uppercase tracking-wider text-sl-text-3">
              {{ r.label }}
            </div>
            <div class="sl-tabular mt-0.5 truncate text-[13px] text-sl-text-1" :title="r.value">
              {{ r.value || "—" }}
            </div>
          </div>
        </div>

        <!-- Always Offroad Mode（对应 sunnylink 弹层里的同名开关） -->
        <div class="mt-3 flex items-center gap-3 border-t border-sl-border pt-3">
          <div class="min-w-0 flex-1">
            <div class="text-[13px] font-medium text-sl-text-1">Always Offroad Mode</div>
            <div class="mt-0.5 text-[11px] leading-snug text-sl-text-3">
              强制设备保持 Offroad，立即生效；重启后自动清除
            </div>
          </div>
          <Switch
            :model-value="alwaysOffroad"
            :pending="isPending('OffroadMode')"
            aria-label="Always Offroad Mode"
            @update:model-value="toggleAlwaysOffroad"
          />
        </div>
      </div>
    </div>
  </div>
</template>
