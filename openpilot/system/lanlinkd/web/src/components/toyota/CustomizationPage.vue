<script setup lang="ts">
/** 定制页：进入时逐个 ECU 扫描当前值（进度条）→ 常用 8 项 + 分类入口；分类子页列出该分类的设置行。
 *
 * 只显示扫描里真正读到的项（ECU 不在线或车上不支持的不显示）。
 * 点设置行弹 SettingSheet，选了值就备份 → 写入 → 校验。
 */
import { computed, onMounted, ref } from "vue";
import { ChevronRight, RefreshCw } from "lucide-vue-next";
import type { ToyotaSetting } from "@/lib/schema";
import { scanTally, settingValueLabel } from "@/lib/toyota";
import { iconByName } from "@/lib/toyotaIcons";
import { currentPage, goTo } from "@/lib/toyotaNav";
import { customizationScanTotal, scanCustomizations, toyotaData } from "@/lib/toyotaData";
import Button from "../ui/Button.vue";
import SettingSheet from "./SettingSheet.vue";

const scan = computed(() => toyotaData.customization);
const catalog = computed(() => toyotaData.catalog!);
const tally = computed(() => scanTally(scan.value.statuses, customizationScanTotal()));

onMounted(() => { if (scan.value.state === "idle") void scanCustomizations(); });

const readable = computed(() => catalog.value.settings.filter((s) => s.key in scan.value.values));
const popular = computed(() => readable.value.filter((s) => s.popular));
const categories = computed(() =>
  catalog.value.categories
    .map((c) => ({ ...c, count: readable.value.filter((s) => s.category === c.id).length }))
    .filter((c) => c.count > 0),
);
const categorySettings = computed(() => readable.value.filter((s) => s.category === currentPage.value.arg));

const editing = ref<ToyotaSetting | null>(null);
const valueText = (s: ToyotaSetting) => settingValueLabel(s, scan.value.values[s.key]?.value);
</script>

<template>
  <div v-if="scan.state !== 'done'" class="sl-card p-5 text-center">
    <b class="text-[15px] text-sl-text-1">正在读取 ECU <span class="sl-tabular">{{ tally.done }}/{{ tally.total }}</span></b>
    <p class="mt-1 text-[13px] text-sl-text-2">读取 {{ catalog.settings.length }} 项当前设置（含 CAN 车窗项）</p>
    <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-sl-surface-3">
      <div class="h-full bg-sl-accent transition-[width] duration-100" :style="{ width: `${tally.percent}%` }" />
    </div>
  </div>

  <template v-else>
    <!-- 分类子页 -->
    <div v-if="currentPage.page === 'customCat'" class="sl-card divide-y divide-sl-border overflow-hidden">
      <button
        v-for="s in categorySettings"
        :key="s.key"
        type="button"
        class="flex w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-sl-surface-2"
        @click="editing = s"
      >
        <span class="flex-1 text-[15px] text-sl-text-1">{{ s.name }}</span>
        <span class="max-w-[44%] text-right text-[14px] font-semibold text-sl-accent">{{ valueText(s) }}</span>
      </button>
    </div>

    <!-- 总览：常用 + 全部分类 -->
    <div v-else class="flex flex-col gap-3">
      <p class="text-[14px] text-sl-text-2">一键修改车辆隐藏/可调功能。每次修改前自动备份，可撤销。</p>
      <p class="text-[12px] text-sl-text-3">读取：IG-ON 或 Ready 均可 · 修改 / 清除：请用 IG-ON，不要进入 Ready</p>

      <div v-if="!readable.length" class="sl-card p-5 text-center text-[13px] text-sl-text-2">
        没有读到任何定制项。
        <template v-if="scan.unreachable.length">无应答或应答异常的 ECU：{{ scan.unreachable.map((e) => e.name).join("、") }}。</template>
      </div>

      <template v-else>
        <template v-if="popular.length">
          <h2 class="mx-1 mt-2 text-[13px] font-semibold tracking-wide text-sl-text-2">常用</h2>
          <div class="sl-card divide-y divide-sl-border overflow-hidden">
            <button
              v-for="s in popular"
              :key="s.key"
              type="button"
              class="flex w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-sl-surface-2"
              @click="editing = s"
            >
              <span class="flex-1 text-[15px] text-sl-text-1">{{ s.name }}</span>
              <span class="max-w-[44%] text-right text-[14px] font-semibold text-sl-accent">{{ valueText(s) }}</span>
            </button>
          </div>
        </template>

        <h2 class="mx-1 mt-2 text-[13px] font-semibold tracking-wide text-sl-text-2">全部分类</h2>
        <div class="sl-card divide-y divide-sl-border overflow-hidden">
          <button
            v-for="c in categories"
            :key="c.id"
            type="button"
            class="flex w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-sl-surface-2"
            @click="goTo('customCat', c.id)"
          >
            <component :is="iconByName(c.icon)" class="size-5 shrink-0 text-sl-text-2" />
            <span class="flex-1 text-[15px] text-sl-text-1">{{ c.name }}</span>
            <span class="text-[13px] text-sl-text-3">{{ c.count }} 项</span>
            <ChevronRight class="size-4 text-sl-text-3" />
          </button>
        </div>
      </template>

      <Button class="self-center" @click="scanCustomizations"><RefreshCw />重新读取</Button>
    </div>
  </template>

  <SettingSheet :setting="editing" @close="editing = null" />
</template>
