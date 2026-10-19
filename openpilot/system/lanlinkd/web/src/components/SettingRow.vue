<script setup lang="ts">
/** 一行设置。按 widget 分派到具体控件，并统一处理徽章/置灰/原因。
 *
 * 分派规则来自 upstream 的 page.schema.json（widget enum 只有五种，
 * **没有 slider**）：`option` 带 options[] → 下拉，带 min/max/step → 步进器。
 * 详见 FRONTEND_SPEC.md §3。
 */
import { computed } from "vue";
import { ChevronRight } from "lucide-vue-next";
import Badge from "./ui/Badge.vue";
import Switch from "./ui/Switch.vue";
import Stepper from "./ui/Stepper.vue";
import Select from "./ui/Select.vue";
import SegmentedButtons from "./ui/SegmentedButtons.vue";
import Button from "./ui/Button.vue";
import { itemState, optionDisabled, resolveTitle } from "@/lib/itemState";
import { boolValue, isPending, numValue, rawValue, store, toggleParam, writeParam } from "@/lib/store";
import { cn, resolveUnit } from "@/lib/utils";
import { pickControl } from "@/lib/control";
import type { Item } from "@/lib/schema";

const props = withDefaults(defineProps<{ item: Item; depth?: number }>(), { depth: 0 });

const ctx = computed(() => ({ params: store.params, caps: store.caps }));
const st = computed(() => itemState(props.item, ctx.value));
const title = computed(() => resolveTitle(props.item, store.params));
const metric = computed(() => store.params.IsMetric === "1");
const unit = computed(() => resolveUnit(props.item.unit, metric.value));
const pending = computed(() => isPending(props.item.key));

/** 控件选型见 lib/control.ts：不只看 widget，还要看数据形状
 *  （选项过多的 multiple_button 会退化成下拉，否则会溢出屏幕）。 */
const kind = computed(() => pickControl(props.item));

const optionState = computed(() => {
  const out: Record<string, { disabled: boolean; reason: string }> = {};
  for (const o of props.item.options ?? []) {
    out[String(o.value)] = optionDisabled(o.enablement, ctx.value);
  }
  return out;
});

/** 行内子项：父项关闭时不显示（避免一堆置灰控件占版面） */
const visibleSubItems = computed(() =>
  (props.item.sub_items ?? []).filter((s) => itemState(s, ctx.value).visible),
);
const showSubItems = computed(
  () => visibleSubItems.value.length > 0 && boolValue(props.item.key),
);

const current = computed(() => rawValue(props.item.key) ?? "");
</script>

<template>
  <div v-if="st.visible" class="flex flex-col">
    <div
      :class="
        cn(
          'flex items-start justify-between gap-4 py-3.5',
          props.depth > 0 && 'pl-4 border-l border-sl-border/60',
        )
      "
    >
      <!-- 文案 -->
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span :class="cn('text-[15px] leading-snug', st.disabled ? 'text-sl-text-3' : 'text-sl-text-1')">
            {{ title }}
          </span>
          <Badge v-for="b in st.badges" :key="b.text" :kind="b.kind" :title="b.title">
            {{ b.text }}
          </Badge>
        </div>

        <p v-if="item.description" class="mt-1 text-[13px] leading-relaxed text-sl-text-2">
          {{ item.description }}
        </p>
        <p v-if="item.details" class="mt-1 text-[13px] leading-relaxed text-sl-text-3">
          {{ item.details }}
        </p>
        <!-- 置灰原因：sunnylink 的关键交互，让用户知道该去哪开 -->
        <p
          v-for="r in st.reasons"
          :key="r"
          class="mt-1 flex items-center gap-1.5 text-[12px] text-sl-warn/90"
        >
          {{ r }}
        </p>
      </div>

      <!-- 控件 -->
      <div class="max-w-[55%] shrink-0 pt-0.5">
        <Switch
          v-if="kind === 'toggle'"
          :model-value="boolValue(item.key)"
          :disabled="st.disabled"
          :pending="pending"
          :aria-label="title"
          @update:model-value="toggleParam(item.key)"
        />

        <Stepper
          v-else-if="kind === 'stepper'"
          :model-value="numValue(item.key, item.min ?? 0)"
          :min="item.min ?? 0"
          :max="item.max ?? 100"
          :step="item.step"
          :unit="unit"
          :disabled="st.disabled"
          :pending="pending"
          @commit="writeParam(item.key, String($event))"
        />

        <Select
          v-else-if="kind === 'select'"
          :model-value="current"
          :options="item.options ?? []"
          :disabled="st.disabled"
          :pending="pending"
          :aria-label="title"
          :option-state="optionState"
          @commit="writeParam(item.key, $event)"
        />

        <SegmentedButtons
          v-else-if="kind === 'segmented'"
          :model-value="current"
          :options="item.options ?? []"
          :disabled="st.disabled"
          :pending="pending"
          :option-state="optionState"
          @commit="writeParam(item.key, $event)"
        />

        <Button v-else-if="kind === 'button'" :disabled="st.disabled" size="sm">
          执行
          <ChevronRight class="size-3.5" />
        </Button>

        <span v-else class="sl-tabular text-sm text-sl-text-2">{{ current || "--" }}</span>
      </div>
    </div>

    <!-- 行内子项：父开关打开后展开 -->
    <div v-if="showSubItems" class="mb-1 ml-1 flex flex-col gap-0">
      <SettingRow
        v-for="sub in visibleSubItems"
        :key="sub.key"
        :item="sub"
        :depth="props.depth + 1"
      />
    </div>
  </div>
</template>

<script lang="ts">
// 递归组件需要显式命名，才能在模板里引用自己
export default { name: "SettingRow" };
</script>
