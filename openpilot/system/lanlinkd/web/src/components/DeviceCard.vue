<script setup lang="ts">
/** 设备卡片。复刻 sunnylink Home 的设备条目：
 *  dongle id + 状态 + 版本 / 分支 / 提交 三段（原站为 VERSION / BRANCH / COMMIT 大写小标签）。
 *  这是 sunnylink 唯一展示的设备信息——没有温度、车速等遥测。 */
import { computed } from "vue";
import { Cpu } from "lucide-vue-next";
import { store } from "@/lib/store";

const sys = computed(() => store.status?.system);

const fields = computed(() => [
  { label: "版本", value: sys.value?.version },
  { label: "分支", value: sys.value?.branch },
  { label: "提交", value: sys.value?.commit?.slice(0, 12) },
]);
</script>

<template>
  <div class="sl-card flex flex-col gap-3 px-4 py-3.5 md:px-5 md:py-4">
    <div class="flex items-center gap-3">
      <div class="grid size-9 shrink-0 place-items-center rounded-lg bg-sl-surface-3 ring-1 ring-inset ring-sl-border">
        <Cpu class="size-4 text-sl-text-2" />
      </div>
      <div class="min-w-0">
        <div class="truncate text-sm font-medium text-sl-text-1">本机设备</div>
        <div class="truncate text-[12px] text-sl-text-3">局域网直连</div>
      </div>
    </div>

    <div class="grid grid-cols-3 gap-3 border-t border-sl-border pt-3">
      <div v-for="f in fields" :key="f.label" class="min-w-0">
        <div class="text-[12px] font-medium text-sl-text-3">
          {{ f.label }}
        </div>
        <div class="sl-tabular mt-0.5 truncate text-[13px] text-sl-text-1" :title="f.value">
          {{ f.value || "—" }}
        </div>
      </div>
    </div>
  </div>
</template>
