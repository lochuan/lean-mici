<script setup lang="ts">
/** TechStream（丰田诊断）页：会话条 + 页面栈。
 *
 * 目录（/api/toyota/catalog）不需要诊断模式，挂载时就拉；拉到之后才渲染页面。
 * 会话结束（主动退出或空闲超时）时回首页并清掉扫描结果：车上的值可能已被别处改动。
 */
import { computed, onMounted, onUnmounted, watch } from "vue";
import { ChevronLeft, Loader2 } from "lucide-vue-next";
import { clearToasts } from "@/lib/store";
import { canGoBack, currentPage, goBack, resetToHome } from "@/lib/toyotaNav";
import { loadCatalog, resetScanData, toyotaData } from "@/lib/toyotaData";
import { startSessionWatch, touch, currentSessionView } from "@/lib/toyotaSession";
import Button from "../ui/Button.vue";
import BackupsPage from "./BackupsPage.vue";
import BatteryPage from "./BatteryPage.vue";
import CustomizationPage from "./CustomizationPage.vue";
import DtcEcuPage from "./DtcEcuPage.vue";
import DtcPage from "./DtcPage.vue";
import HomePage from "./HomePage.vue";
import LivePage from "./LivePage.vue";
import SessionBar from "./SessionBar.vue";

const PAGE_TITLES: Record<string, string> = {
  home: "丰田诊断", custom: "定制", dtc: "故障码", live: "实时数据", battery: "动力电池", backups: "备份记录",
};

const title = computed(() => {
  const { page, arg } = currentPage.value;
  const catalog = toyotaData.catalog;
  if (page === "customCat") return catalog?.categories.find((c) => c.id === arg)?.name ?? "定制";
  if (page === "dtcEcu") return catalog?.ecus.find((e) => e.id === arg)?.name ?? "故障码";
  if (page === "liveTool") return catalog?.liveTools.find((t) => t.id === arg)?.name ?? "实时数据";
  return PAGE_TITLES[page] ?? "丰田诊断";
});

let stopWatch: (() => void) | undefined;
onMounted(() => {
  stopWatch = startSessionWatch();
  // 弹层 / toast 渲染在 body 下，只能在 document 上捕获点击
  document.addEventListener("click", touch, true);
  void loadCatalog();
});
onUnmounted(() => {
  stopWatch?.();
  document.removeEventListener("click", touch, true);
  clearToasts();
});

// 备份记录不需要诊断模式，所以停在备份页时会话结束不踢回首页
watch(() => currentSessionView.value.canUseDiagnostics, (on, wasOn) => {
  if (wasOn && !on) {
    resetScanData();
    if (currentPage.value.page !== "backups") resetToHome();
  }
});
</script>

<template>
  <div class="mx-auto flex w-full max-w-[480px] flex-col gap-3">
    <SessionBar />

    <div v-if="!toyotaData.catalog" class="sl-card flex flex-col items-center gap-3 px-5 py-8 text-center text-[13px] text-sl-text-2">
      <template v-if="toyotaData.catalogError">
        <p class="text-sl-danger">{{ toyotaData.catalogError }}</p>
        <Button @click="loadCatalog">重试</Button>
      </template>
      <Loader2 v-else class="size-5 animate-spin text-sl-text-3" />
    </div>

    <template v-else>
      <header v-if="canGoBack" class="flex items-center gap-1">
        <Button variant="ghost" size="sm" aria-label="返回" @click="goBack"><ChevronLeft />返回</Button>
        <h2 class="text-[17px] font-semibold text-sl-text-1">{{ title }}</h2>
      </header>

      <HomePage v-if="currentPage.page === 'home'" />
      <CustomizationPage v-else-if="currentPage.page === 'custom' || currentPage.page === 'customCat'" />
      <DtcPage v-else-if="currentPage.page === 'dtc'" />
      <DtcEcuPage v-else-if="currentPage.page === 'dtcEcu'" />
      <LivePage v-else-if="currentPage.page === 'live' || currentPage.page === 'liveTool'" />
      <BatteryPage v-else-if="currentPage.page === 'battery'" />
      <BackupsPage v-else-if="currentPage.page === 'backups'" />
    </template>
  </div>
</template>
