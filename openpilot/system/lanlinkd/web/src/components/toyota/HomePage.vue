<script setup lang="ts">
/** 首页：车辆卡（12V、里程、在线 ECU 数）+ 5 个功能入口。
 *
 * 未进入诊断模式时前四个入口置灰，点了会抖动会话条并提示（备份记录不需要诊断模式）。
 */
import { computed, onMounted, ref, watch, type Component } from "vue";
import { Activity, Archive, BatteryCharging, CarFront, ChevronRight, Stethoscope, Wrench } from "lucide-vue-next";
import { api } from "@/lib/api";
import { BATTERY_LEVEL_LABEL } from "@/lib/battery";
import type { ToyotaLiveValue } from "@/lib/schema";
import { goTo, type ToyotaPageName } from "@/lib/toyotaNav";
import { BATTERY_12V_KEY, HOME_LIVE_KEYS, MILEAGE_KEY, MILEAGE_UNIT_KEY, dtcSummaryText, liveValueText, mileageText, onlineEcuText } from "@/lib/toyota";
import { toyotaData } from "@/lib/toyotaData";
import { requireActive, currentSessionView } from "@/lib/toyotaSession";
import { cn } from "@/lib/utils";
import Badge from "../ui/Badge.vue";

const vehicle = ref<Map<string, ToyotaLiveValue>>(new Map());
const catalog = computed(() => toyotaData.catalog);

async function readVehicle(): Promise<void> {
  try {
    const { values } = await api.toyotaReadLive([...HOME_LIVE_KEYS]);
    vehicle.value = new Map(values.map((v) => [v.key, v]));
  } catch {
    // 车辆卡只是附带信息：读不到显示「—」，不打扰用户
  }
}

onMounted(() => { if (currentSessionView.value.canUseDiagnostics) void readVehicle(); });
watch(() => currentSessionView.value.canUseDiagnostics, (on) => { if (on) void readVehicle(); else vehicle.value = new Map(); });

const voltageText = computed(() => liveValueText(vehicle.value.get(BATTERY_12V_KEY), "V"));
const mileageDisplay = computed(() => mileageText(vehicle.value.get(MILEAGE_KEY), vehicle.value.get(MILEAGE_UNIT_KEY)));

const batteryLevel = computed(() => toyotaData.battery.analysis?.level);

interface Entry { page: ToyotaPageName; icon: Component; title: string; desc: string; needsSession: boolean }
const entries = computed<Entry[]>(() => [
  { page: "custom", icon: Wrench, title: "定制", desc: `${catalog.value?.settings.length ?? 0} 项可调 · 按分类一键修改`, needsSession: true },
  { page: "dtc", icon: Stethoscope, title: "故障码", desc: dtcSummaryText(toyotaData.dtc.state === "done" ? toyotaData.dtc.scans : null), needsSession: true },
  { page: "live", icon: Activity, title: "实时数据", desc: "车轮速度、发动机与 12V、空调", needsSession: true },
  { page: "battery", icon: BatteryCharging, title: "动力电池", desc: "12 块电池电压与内阻", needsSession: true },
  { page: "backups", icon: Archive, title: "备份记录", desc: "修改前会自动备份，可随时恢复", needsSession: false },
]);

function open(e: Entry): void {
  if (e.needsSession && !requireActive()) return;
  goTo(e.page);
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <section class="sl-card flex flex-col gap-3 p-4">
      <div class="flex items-center gap-3">
        <CarFront class="size-9 shrink-0 text-sl-accent" />
        <div>
          <div class="text-[18px] font-semibold text-sl-text-1">Toyota Sienna 第 4 代</div>
          <div class="text-[13px] text-sl-text-2">混合动力 · 2021+ · 镍氢 288 V</div>
        </div>
      </div>
      <div class="grid grid-cols-3 gap-2">
        <div class="rounded-lg bg-sl-surface-2 px-2.5 py-2">
          <div class="text-[12px] text-sl-text-2">12V 电压</div>
          <div class="sl-tabular text-[16px] font-semibold text-sl-text-1">{{ voltageText }}</div>
        </div>
        <div class="rounded-lg bg-sl-surface-2 px-2.5 py-2">
          <div class="text-[12px] text-sl-text-2">总里程</div>
          <div class="sl-tabular text-[16px] font-semibold text-sl-text-1">{{ mileageDisplay }}</div>
        </div>
        <div class="rounded-lg bg-sl-surface-2 px-2.5 py-2">
          <div class="text-[12px] text-sl-text-2">在线 ECU</div>
          <div class="sl-tabular text-[16px] font-semibold text-sl-text-1">
            {{ onlineEcuText(toyotaData.ecuAnswered) }}
          </div>
        </div>
      </div>
      <p class="text-[12px] text-sl-text-3">12V 电压：IG-ON 看蓄电池，Ready 看充电。在线 ECU：应答数 / 已探测数，扫描后更新。</p>
    </section>

    <button
      v-for="e in entries"
      :key="e.page"
      type="button"
      :class="cn('sl-card flex w-full items-center gap-3.5 p-3.5 text-left transition-colors hover:bg-sl-surface-2', e.needsSession && !currentSessionView.canUseDiagnostics && 'opacity-40')"
      @click="open(e)"
    >
      <span class="grid size-11 shrink-0 place-items-center rounded-xl bg-sl-accent-muted text-sl-accent">
        <component :is="e.icon" class="size-5" />
      </span>
      <span class="flex min-w-0 flex-1 flex-col">
        <b class="text-[16px] font-semibold text-sl-text-1">{{ e.title }}</b>
        <span class="text-[13px] text-sl-text-2">
          {{ e.desc }}
          <template v-if="e.page === 'battery' && batteryLevel">
            · 结论 <Badge :kind="BATTERY_LEVEL_LABEL[batteryLevel].tone">{{ BATTERY_LEVEL_LABEL[batteryLevel].text }}</Badge>
          </template>
        </span>
      </span>
      <ChevronRight class="size-5 shrink-0 text-sl-text-3" />
    </button>

    <p v-if="!currentSessionView.canUseDiagnostics" class="text-center text-[13px] text-sl-text-2">
      先点上方「进入」开启诊断模式，才能使用前四项功能
    </p>
  </div>
</template>
