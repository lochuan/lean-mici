<script setup lang="ts">
/** 面板页：标题 + 各分区 + 车型专属设置。 */
import { computed, ref, watch } from "vue";
import SettingSection from "./SettingSection.vue";
import SubPanelDrawer from "./SubPanelDrawer.vue";
import SettingRow from "./SettingRow.vue";
import ModelsManager from "./ModelsManager.vue";
import VehiclePanel from "./VehiclePanel.vue";
import StatusPanel from "./StatusPanel.vue";
import Badge from "./ui/Badge.vue";
import { itemState } from "@/lib/itemState";
import { store } from "@/lib/store";
import type { Panel, SubPanel } from "@/lib/schema";

const props = defineProps<{ panel: Panel; highlight?: string }>();

const openId = ref<string | null>(null);

const ctx = computed(() => ({ params: store.params, caps: store.caps }));

const allSubPanels = computed<SubPanel[]>(() =>
  props.panel.sections.flatMap((s) => s.sub_panels ?? []),
);
const openPanel = computed(() => allSubPanels.value.find((s) => s.id === openId.value) ?? null);

/** 仅当前车型品牌的专属设置 */
const brandSettings = computed(() => {
  const brand = String(store.caps.brand ?? "");
  const group = store.schema?.vehicle_settings?.[brand];
  if (!group) return null;
  const items = group.items.filter((i) => itemState(i, ctx.value).visible);
  return items.length ? { ...group, items } : null;
});

// 只在「车辆」相关面板底部显示品牌设置，避免每页重复
const showBrand = computed(() => brandSettings.value && props.panel.id === "toggles");

watch(
  () => props.panel.id,
  () => {
    openId.value = null;
  },
);
</script>

<template>
  <div class="mx-auto flex w-full max-w-[768px] flex-col gap-4 px-6 py-6">
    <header class="flex flex-col gap-1">
      <div class="flex items-center gap-2">
        <h1 class="text-[22px] font-semibold tracking-tight text-sl-text-1">{{ panel.label }}</h1>
        <Badge v-if="panel.remote_configurable" kind="info" title="此面板支持远程配置">
          可远程配置
        </Badge>
      </div>
      <p v-if="panel.description" class="text-[13px] text-sl-text-2">{{ panel.description }}</p>
    </header>

    <!-- 模型页：设置项之前先放模型管理（与 sunnylink 的版面顺序一致） -->
    <ModelsManager v-if="panel.id === 'models'" />
    <VehiclePanel v-else-if="panel.id === 'vehicle'" />
    <!-- 状态页：遥测 + 雷达点阵，整个面板都是自定义组件 -->
    <StatusPanel v-else-if="panel.id === 'status'" />

    <SettingSection
      v-for="(section, i) in panel.sections"
      :key="section.id ?? `s${i}`"
      :section="section"
      @open="openId = $event"
    />

    <!-- 车型专属：按 capabilities.brand 选取 -->
    <section v-if="showBrand && brandSettings" class="sl-card px-5 py-4">
      <h2 class="mb-1 text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
        {{ brandSettings.title }}
      </h2>
      <div class="divide-y divide-sl-border/70">
        <SettingRow v-for="item in brandSettings.items" :key="item.key" :item="item" />
      </div>
    </section>

    <SubPanelDrawer :panel="openPanel" @close="openId = null" />
  </div>
</template>
