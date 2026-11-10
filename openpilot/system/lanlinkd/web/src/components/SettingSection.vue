<script setup lang="ts">
/** 设置分区：标题 + 若干行 + 子设置原地手风琴。 */
import { computed } from "vue";
import Accordion, { type AccordionEntry } from "./ui/Accordion.vue";
import SettingRow from "./SettingRow.vue";
import Badge from "./ui/Badge.vue";
import { evalRules } from "@/lib/rules";
import { itemState, subPanelOpenable } from "@/lib/itemState";
import { store } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { Section } from "@/lib/schema";

const props = defineProps<{ section: Section }>();

const ctx = computed(() => ({ params: store.params, caps: store.caps }));

const visible = computed(() => evalRules(props.section.visibility, ctx.value).ok);
const enabled = computed(() => evalRules(props.section.enablement, ctx.value));

const rows = computed(() =>
  (props.section.items ?? []).filter((i) => itemState(i, ctx.value).visible),
);

const accordions = computed<AccordionEntry[]>(() =>
  (props.section.sub_panels ?? []).map((sp) => {
    const gate = subPanelOpenable(sp.trigger_condition, ctx.value);
    return {
      id: sp.id,
      label: sp.label,
      count: sp.items.length,
      disabled: !gate.ok,
      disabledReason: gate.reason,
    };
  }),
);

function subRows(id: string) {
  const sp = (props.section.sub_panels ?? []).find((s) => s.id === id);
  return (sp?.items ?? []).filter((i) => itemState(i, ctx.value).visible);
}

/** 只有一行、没有子设置的分区：手机上收紧上下留白（控件本身尺寸不变，触控区不缩） */
const single = computed(() => rows.value.length === 1 && accordions.value.length === 0);
</script>

<template>
  <section
    v-if="visible"
    :class="cn('sl-card', single ? 'px-4 py-2.5 md:px-5 md:py-4' : 'px-4 py-3 md:px-5 md:py-4')"
  >
    <header v-if="section.title || section.description" :class="single ? 'md:mb-1' : 'mb-1'">
      <div class="flex items-center gap-2">
        <h2 class="text-[13px] font-semibold tracking-wide text-sl-text-3">
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
        <SettingRow v-for="item in rows" :key="item.key" :item="item" :compact="single" />
      </div>

      <!-- 子设置原地展开：前置开关未开时置灰 + Lock + title 说明，而不是消失 -->
      <Accordion
        v-if="accordions.length"
        :items="accordions"
      >
        <template #default="it">
          <div class="divide-y divide-sl-border/70 py-1">
            <SettingRow v-for="i in subRows(it.item.id)" :key="i.key" :item="i" />
          </div>
        </template>
      </Accordion>
    </div>
  </section>
</template>
