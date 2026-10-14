<script setup lang="ts">
/** 动力电池页：结论卡、6 个指标卡、两张柱状图、「判定依据」折叠、重新读取。
 *  电池读数就是 24 个实时数据项（12 块电压 + 12 块内阻），复用 live/read，不另开后端接口。
 *  SOC、电池温度这台车读不到，页面不显示。 */
import { computed, onMounted, ref } from "vue";
import { CircleAlert, CircleCheck, Loader2, RefreshCw, TriangleAlert } from "lucide-vue-next";
import { api } from "@/lib/api";
import { analyzeBattery, batteryBlocksFromValues, BATTERY_LEVEL_LABEL, DANGER_DIFF_V, WARN_DIFF_V, type BatteryLevel } from "@/lib/battery";
import { toast } from "@/lib/store";
import { errorText, formatNumber } from "@/lib/toyota";
import { toyotaData } from "@/lib/toyotaData";
import { cn } from "@/lib/utils";
import BarChart from "../ui/BarChart.vue";
import Button from "../ui/Button.vue";

const loading = ref(false);
const failed = ref(false);
const analysis = computed(() => toyotaData.battery.analysis);

async function read(): Promise<void> {
  const battery = toyotaData.catalog?.battery;
  if (!battery || loading.value) return;
  loading.value = true;
  try {
    const keys = battery.blocks.flatMap((b) => [b.voltageKey, b.resistanceKey]);
    const { values } = await api.toyotaReadLive(keys);
    const readings = batteryBlocksFromValues(battery, values);
    toyotaData.battery.analysis = readings ? analyzeBattery(readings) : null;
    failed.value = !readings;
  } catch (e) {
    failed.value = true;
    toast(errorText(e), "error");
  } finally {
    loading.value = false;
  }
}

onMounted(() => { if (!analysis.value) void read(); });

const LEVEL: Record<BatteryLevel, { icon: typeof CircleCheck; color: string; border: string }> = {
  ok: { icon: CircleCheck, color: "text-sl-accent", border: "border-l-sl-accent" },
  warn: { icon: TriangleAlert, color: "text-sl-warn", border: "border-l-sl-warn" },
  danger: { icon: CircleAlert, color: "text-sl-danger", border: "border-l-sl-danger" },
};

const meanV2 = computed(() => (analysis.value ? analysis.value.blocks.reduce((s, b) => s + b.voltagePerTwoModules, 0) / analysis.value.blocks.length : 0));
const voltageChart = computed(() => {
  const a = analysis.value!;
  const edgeTone = a.diff > DANGER_DIFF_V ? "danger" : a.diff > WARN_DIFF_V ? "warn" : "accent";
  return {
    values: a.blocks.map((b) => b.voltagePerTwoModules),
    tones: a.blocks.map((b) => (a.lowBlocks.includes(b) ? "danger" : b === a.min || b === a.max ? edgeTone : "accent")),
    marks: a.blocks.map((b) => (b === a.max ? "最高" : b === a.min ? "最低" : "")),
    lo: Math.floor((meanV2.value - 0.7) * 10) / 10,
    hi: Math.ceil((meanV2.value + 0.5) * 10) / 10,
  };
});
const resistanceChart = computed(() => {
  const a = analysis.value!;
  return {
    values: a.blocks.map((b) => b.resistance),
    tones: a.blocks.map((b) => (a.highResistanceBlocks.includes(b) ? "warn" : "info")),
    marks: a.blocks.map(() => ""),
    hi: Math.max(30, Math.ceil(a.resistanceMaxBlock.resistance / 5) * 5),
  };
});
const wholeBlocks = computed(() => analysis.value?.blocks.filter((b) => b.modules === 4).length ?? 0);
const halfBlocks = computed(() => analysis.value?.blocks.filter((b) => b.modules === 2).length ?? 0);
const moduleCount = computed(() => analysis.value?.blocks.reduce((s, b) => s + b.modules, 0) ?? 0);
</script>

<template>
  <div v-if="!analysis" class="sl-card flex flex-col items-center gap-3 p-6 text-center text-[13px] text-sl-text-2">
    <Loader2 v-if="loading" class="size-5 animate-spin text-sl-text-3" />
    <template v-else>
      <p class="text-sl-danger">{{ failed ? "电池读数读取失败（动力电池 ECU 无应答或读数不完整）" : "尚未读取" }}</p>
      <Button @click="read"><RefreshCw />重新读取</Button>
    </template>
  </div>

  <div v-else class="flex flex-col gap-3">
    <section :class="cn('sl-card flex items-start gap-3.5 border-l-4 p-4', LEVEL[analysis.level].border)">
      <component :is="LEVEL[analysis.level].icon" :class="cn('mt-0.5 size-8 shrink-0', LEVEL[analysis.level].color)" />
      <div>
        <b :class="cn('text-[20px]', LEVEL[analysis.level].color)">{{ BATTERY_LEVEL_LABEL[analysis.level].text }}</b>
        <p class="mt-0.5 text-[13px] text-sl-text-2">{{ analysis.levelReason }}</p>
      </div>
    </section>
    <p class="mx-1 text-[12px] text-sl-text-3">建议 Ready 下读取；刚开完车读数更能反映弱块</p>

    <div class="grid grid-cols-2 gap-2">
      <div class="sl-card col-span-2 px-3 py-2.5">
        <div class="text-[12px] text-sl-text-2">总电压（12 块求和）</div>
        <b class="sl-tabular text-[16px] text-sl-text-1">{{ formatNumber(analysis.total, 1) }} V</b>
        <span class="ml-2 text-[12px] text-sl-text-2">标称 288 V，静置略高属正常</span>
      </div>
      <div class="sl-card col-span-2 px-3 py-2.5">
        <div class="text-[12px] text-sl-text-2">电池结构</div>
        <b class="text-[16px] text-sl-text-1">{{ wholeBlocks }} 整块 + {{ halfBlocks }} 半块 = {{ moduleCount }} 模组</b>
        <div class="text-[12px] text-sl-text-2">整块 4 模组 / 半块 2 模组</div>
      </div>
      <div class="sl-card px-3 py-2.5">
        <div class="text-[12px] text-sl-text-2">电压差（每 2 模组）</div>
        <b class="sl-tabular text-[16px] text-sl-text-1">{{ formatNumber(analysis.diff, 2) }} V</b>
      </div>
      <div class="sl-card px-3 py-2.5">
        <div class="text-[12px] text-sl-text-2">内阻 平均 / 最大</div>
        <b class="sl-tabular text-[16px] text-sl-text-1">{{ formatNumber(analysis.resistanceAvg, 1) }} / {{ analysis.resistanceMaxBlock.resistance }} mΩ</b>
      </div>
      <div class="sl-card px-3 py-2.5">
        <div class="text-[12px] text-sl-text-2">最高块</div>
        <b class="sl-tabular text-[16px] text-sl-text-1">#{{ analysis.max.blockNumber }} · {{ formatNumber(analysis.max.voltagePerTwoModules, 2) }} V</b>
      </div>
      <div class="sl-card px-3 py-2.5">
        <div class="text-[12px] text-sl-text-2">最低块</div>
        <b class="sl-tabular text-[16px] text-sl-text-1">#{{ analysis.min.blockNumber }} · {{ formatNumber(analysis.min.voltagePerTwoModules, 2) }} V</b>
      </div>
    </div>

    <BarChart
      title="各块电压（折算到每 2 模组）"
      subtitle="纵轴已截断，仅用于对比块间差异；半块按实测、整块 ÷2"
      :digits="2"
      v-bind="voltageChart"
    />
    <BarChart
      title="各块内阻（mΩ）"
      subtitle="整块 / 半块分别和同类块比较，超出同类平均 20% 标为黄色"
      :digits="0"
      :lo="0"
      v-bind="resistanceChart"
    />

    <details class="sl-card px-3.5 py-3">
      <summary class="cursor-pointer font-semibold text-sl-text-1">判定依据</summary>
      <ul class="mt-2 list-disc space-y-1 pl-[18px] text-[14px] text-sl-text-2">
        <li>先把每块电压折算到「每 2 模组」：半块（2 模组）不变，整块（4 模组）÷2。</li>
        <li><b class="text-sl-accent">健康</b>：最大 − 最小 ≤ {{ WARN_DIFF_V }} V。</li>
        <li><b class="text-sl-warn">注意</b>：{{ WARN_DIFF_V }} – {{ DANGER_DIFF_V }} V。</li>
        <li><b class="text-sl-danger">异常</b>：&gt; {{ DANGER_DIFF_V }} V，或任一块比其他块平均低 1 V 以上。</li>
        <li>内阻：整块、半块分别求平均，任一块超出同类平均 20% → 注意。</li>
      </ul>
      <p class="mt-2 text-[14px] text-sl-text-2">
        <b>局限：</b>没有丰田官方阈值，这里按块间差异判断，仅供参考；这是静置/驻车读数，负载（加速、爬坡、快速充放电）下的差异更能暴露问题；电池 SOC、温度这台车目前读不到。
      </p>
    </details>

    <Button variant="accent" class="w-full" :disabled="loading" @click="read">
      <Loader2 v-if="loading" class="animate-spin" /><RefreshCw v-else />
      {{ loading ? "正在读取动力电池…" : "重新读取" }}
    </Button>
    <p class="mx-1 text-[12px] text-sl-text-3">来源：动力电池 ECU（0x{{ toyotaData.catalog!.battery.ecu }}）</p>
  </div>
</template>
