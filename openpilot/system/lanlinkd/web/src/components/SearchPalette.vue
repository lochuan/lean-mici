<script setup lang="ts">
/** ⌘K 搜索面板。复刻 sunnylink 的设置搜索。 */
import { computed, nextTick, ref, watch } from "vue";
import { CornerDownLeft, Search } from "lucide-vue-next";
import { searchSettings } from "@/lib/store";
import { cn } from "@/lib/utils";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; pick: [string, string] }>();

const q = ref("");
const active = ref(0);
const input = ref<HTMLInputElement | null>(null);

const hits = computed(() => searchSettings(q.value));

watch(
  () => props.open,
  async (o) => {
    if (o) {
      q.value = "";
      active.value = 0;
      await nextTick();
      input.value?.focus();
    }
  },
);

watch(hits, () => {
  active.value = 0;
});

function onKey(e: KeyboardEvent): void {
  if (e.key === "Escape") return emit("close");
  if (!hits.value.length) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    active.value = (active.value + 1) % hits.value.length;
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    active.value = (active.value - 1 + hits.value.length) % hits.value.length;
  } else if (e.key === "Enter") {
    e.preventDefault();
    const h = hits.value[active.value];
    if (h) emit("pick", h.panel.id, h.item.key);
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition-opacity duration-150"
      leave-active-class="transition-opacity duration-100"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="open"
        class="fixed inset-0 z-[60] flex items-start justify-center bg-black/70 px-4 pt-[12vh] backdrop-blur-sm"
        @click.self="emit('close')"
      >
        <div
          class="w-full max-w-[560px] overflow-hidden rounded-xl border border-sl-border bg-sl-surface shadow-2xl shadow-black/70"
        >
          <div class="flex items-center gap-3 border-b border-sl-border px-4">
            <Search class="size-4 shrink-0 text-sl-text-3" />
            <input
              ref="input"
              v-model="q"
              type="text"
              placeholder="搜索设置项…"
              class="h-12 flex-1 bg-transparent text-[15px] text-sl-text-1 outline-none placeholder:text-sl-text-3"
              @keydown="onKey"
            />
            <kbd class="rounded border border-sl-border bg-sl-surface-2 px-1.5 py-0.5 text-[10px] text-sl-text-3">
              ESC
            </kbd>
          </div>

          <div class="max-h-[50vh] overflow-y-auto p-1.5">
            <p v-if="!q" class="px-3 py-6 text-center text-[13px] text-sl-text-3">
              输入关键词搜索设置项名称或参数名
            </p>
            <p v-else-if="!hits.length" class="px-3 py-6 text-center text-[13px] text-sl-text-3">
              没有匹配「{{ q }}」的设置
            </p>

            <button
              v-for="(h, i) in hits"
              :key="`${h.panel.id}.${h.item.key}`"
              type="button"
              :class="
                cn(
                  'flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition-colors',
                  i === active ? 'bg-sl-surface-3' : 'hover:bg-sl-surface-2',
                )
              "
              @click="emit('pick', h.panel.id, h.item.key)"
              @mouseenter="active = i"
            >
              <span class="min-w-0">
                <span class="block truncate text-sm text-sl-text-1">{{ h.item.title }}</span>
                <span class="block truncate text-[11px] text-sl-text-3">
                  {{ h.panel.label }}
                  <template v-if="h.subPanelLabel"> › {{ h.subPanelLabel }}</template>
                  · {{ h.item.key }}
                </span>
              </span>
              <CornerDownLeft v-if="i === active" class="size-3.5 shrink-0 text-sl-text-3" />
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
