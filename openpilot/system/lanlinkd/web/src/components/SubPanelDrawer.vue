<script setup lang="ts">
/** 抽屉：sub_panel 的展开形态，从右侧滑入。 */
import { ArrowLeft } from "lucide-vue-next";
import SettingRow from "./SettingRow.vue";
import { computed } from "vue";
import { itemState } from "@/lib/itemState";
import { store } from "@/lib/store";
import type { SubPanel } from "@/lib/schema";

const props = defineProps<{ panel: SubPanel | null }>();
const emit = defineEmits<{ close: [] }>();

const ctx = computed(() => ({ params: store.params, caps: store.caps }));
const rows = computed(() =>
  (props.panel?.items ?? []).filter((i) => itemState(i, ctx.value).visible),
);
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition-opacity duration-200"
      leave-active-class="transition-opacity duration-150"
      enter-from-class="opacity-0"
      leave-to-class="opacity-0"
    >
      <div
        v-if="panel"
        class="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
        @click="emit('close')"
      />
    </Transition>

    <Transition
      enter-active-class="transition-transform duration-250 ease-out"
      leave-active-class="transition-transform duration-200 ease-in"
      enter-from-class="translate-x-full"
      leave-to-class="translate-x-full"
    >
      <aside
        v-if="panel"
        class="fixed inset-y-0 right-0 z-50 flex w-full max-w-[520px] flex-col border-l border-sl-border bg-sl-bg shadow-2xl"
      >
        <header class="sl-hairline flex h-16 shrink-0 items-center gap-3 px-5">
          <button
            type="button"
            class="grid size-9 place-items-center rounded-lg text-sl-text-2 transition-colors hover:bg-sl-surface-2 hover:text-sl-text-1"
            aria-label="返回"
            @click="emit('close')"
          >
            <ArrowLeft class="size-5" />
          </button>
          <h2 class="text-base font-semibold text-sl-text-1">{{ panel.label }}</h2>
        </header>

        <div class="flex-1 overflow-y-auto px-5 py-2">
          <div class="divide-y divide-sl-border/70">
            <SettingRow v-for="item in rows" :key="item.key" :item="item" />
          </div>
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>
