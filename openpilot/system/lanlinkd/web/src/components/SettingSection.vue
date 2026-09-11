<script setup lang="ts">
/** 设置分区：标题 + 若干行 + 抽屉入口。 */
import { computed } from "vue";
import { ChevronRight, Lock } from "lucide-vue-next";
import SettingRow from "./SettingRow.vue";
import Badge from "./ui/Badge.vue";
import { evalRules } from "@/lib/rules";
import { itemState, subPanelOpenable } from "@/lib/itemState";
import { store } from "@/lib/store";
import type { Section } from "@/lib/schema";

const props = defineProps<{ section: Section }>();
const emit = defineEmits<{ open: [string] }>();

const ctx = computed(() => ({ params: store.params, caps: store.caps }));

const visible = computed(() => evalRules(props.section.visibility, ctx.value).ok);
const enabled = computed(() => evalRules(props.section.enablement, ctx.value));

const rows = computed(() =>
  (props.section.items ?? []).filter((i) => itemState(i, ctx.value).visible),
);

const drawers = computed(() =>
  (props.section.sub_panels ?? []).map((sp) => ({
    ...sp,
    gate: subPanelOpenable(sp.trigger_condition, ctx.value),
  })),
);
</script>

<template>
  <section v-if="visible" class="sl-card px-5 py-4">
    <header v-if="section.title || section.description" class="mb-1">
      <div class="flex items-center gap-2">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
          {{ section.title }}
        </h2>
        <Badge v-if="!enabled.ok" kind="muted">不可用</Badge>
      </div>
      <p v-if="section.description" class="mt-1 text-[13px] text-sl-text-2">
        {{ section.description }}
      </p>
      <p v-if="!enabled.ok && enabled.reasons.length" class="mt-1 text-[12px] text-sl-warn/90">
        {{ enabled.reasons[0] }}
      </p>
    </header>

    <div :class="!enabled.ok && 'pointer-events-none opacity-45'">
      <div class="divide-y divide-sl-border/70">
        <SettingRow v-for="item in rows" :key="item.key" :item="item" />
      </div>

      <!-- 抽屉入口：前置开关未开时置灰并说明，而不是消失 -->
      <div v-if="drawers.length" class="mt-2 flex flex-col gap-1.5">
        <button
          v-for="d in drawers"
          :key="d.id"
          type="button"
          :disabled="!d.gate.ok"
          :title="d.gate.reason"
          class="group flex h-12 w-full items-center justify-between rounded-lg bg-sl-surface-2 px-4 text-left ring-1 ring-inset ring-sl-border transition-colors hover:bg-sl-surface-3 disabled:pointer-events-none disabled:opacity-40"
          @click="emit('open', d.id)"
        >
          <span class="flex items-center gap-2 text-sm text-sl-text-1">
            <Lock v-if="!d.gate.ok" class="size-3.5 text-sl-text-3" />
            {{ d.label }}
            <span class="text-xs text-sl-text-3">{{ d.items.length }} 项</span>
          </span>
          <ChevronRight class="size-4 text-sl-text-3 transition-transform group-hover:translate-x-0.5" />
        </button>
      </div>
    </div>
  </section>
</template>
