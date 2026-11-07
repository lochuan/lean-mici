<script setup lang="ts">
/** 单个 ECU 的故障码：代码、说明、状态标签；历史通信丢失码折成一组；清除此 ECU。 */
import { computed } from "vue";
import { CircleCheck } from "lucide-vue-next";
import { dtcDescription, dtcScanState, dtcStatusTags, ecuProtocolText, groupDtcs } from "@/lib/toyota";
import { currentPage } from "@/lib/toyotaNav";
import { toyotaData } from "@/lib/toyotaData";
import Badge from "../ui/Badge.vue";
import DtcClearButton from "./DtcClearButton.vue";

const ecu = computed(() => toyotaData.catalog!.ecus.find((e) => e.id === currentPage.value.arg)!);
const scan = computed(() => toyotaData.dtc.scans.find((s) => s.ecu === ecu.value.id));
const groups = computed(() => groupDtcs(scan.value?.dtcs ?? []));
</script>

<template>
  <div class="flex flex-col gap-3">
    <p class="mx-1 text-[13px] text-sl-text-2">{{ ecuProtocolText(ecu.protocol) }} · {{ ecu.id.replace("/", " 子地址 ") }}</p>

    <div v-if="scan?.status !== 'ok'" class="sl-card p-5 text-center text-[13px] text-sl-text-2">
      {{ scan?.status === "malformed" ? "此 ECU 应答异常，已跳过" : "此 ECU 无应答（不在线或未上电）" }}
    </div>
    <div v-else-if="dtcScanState(scan) === 'rejected'" class="sl-card p-5 text-center text-[13px] text-sl-text-2">
      ECU 拒绝读码（{{ scan.readError }}）
    </div>
    <div v-else-if="!scan.dtcs.length" class="sl-card flex flex-col items-center gap-1 p-5 text-[15px] text-sl-text-1">
      <CircleCheck class="size-8 text-sl-accent" />
      此 ECU 没有故障码
    </div>

    <template v-else>
      <div v-if="groups.shown.length" class="sl-card divide-y divide-sl-border overflow-hidden">
        <div v-for="d in groups.shown" :key="d.code" class="px-3.5 py-3">
          <b class="font-mono text-[15px] text-sl-text-1">{{ d.code }}</b>
          <div class="mt-0.5 text-[13px] text-sl-text-2">{{ dtcDescription(d) }}</div>
          <div class="mt-1 flex flex-wrap gap-1">
            <Badge v-for="t in dtcStatusTags(d.status)" :key="t.label" :kind="t.tone">{{ t.label }}</Badge>
          </div>
        </div>
      </div>

      <details v-if="groups.history.length" class="sl-card px-3.5 py-3">
        <summary class="cursor-pointer text-[15px] font-semibold text-sl-text-1">历史通信丢失（{{ groups.history.length }} 条）</summary>
        <p class="mt-1 text-[13px] text-sl-text-2">很可能是拆装线束或断电留下的历史记录</p>
        <div class="mt-2 divide-y divide-sl-border">
          <div v-for="d in groups.history" :key="d.code" class="py-2">
            <b class="font-mono text-[14px] text-sl-text-1">{{ d.code }}</b>
            <div class="mt-0.5 text-[13px] text-sl-text-2">{{ dtcDescription(d) }}</div>
          </div>
        </div>
      </details>

      <DtcClearButton class="w-full" label="清除此 ECU" :ecu-ids="[ecu.id]" />
    </template>
  </div>
</template>
