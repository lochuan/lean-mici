<script setup lang="ts">
/** 故障码页：扫描 → 按 ECU 分组 → 勾选 → 确认清除 → 每个 ECU 的结果。 */
import { computed, ref } from "vue";
import { Eraser, Loader2, ScanSearch } from "lucide-vue-next";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { ToyotaDtcClearResult, ToyotaDtcEcu } from "@/lib/schema";
import { errorText } from "@/lib/toyota";
import Badge from "../ui/Badge.vue";
import Button from "../ui/Button.vue";
import Checkbox from "../ui/Checkbox.vue";
import ConfirmDialog from "./ConfirmDialog.vue";

const ecus = ref<ToyotaDtcEcu[] | null>(null);
const scanning = ref(false);
const selected = ref<string[]>([]);
const clearing = ref(false);
const confirmOpen = ref(false);
const results = ref<ToyotaDtcClearResult[] | null>(null);

const nameOf = (ecu: string) => ecus.value?.find((e) => e.ecu === ecu)?.name ?? "";
const canClear = computed(() => selected.value.length > 0 && !clearing.value);

function setSelected(ecu: string, on: boolean): void {
  selected.value = on ? [...selected.value, ecu] : selected.value.filter((e) => e !== ecu);
}

async function scan(): Promise<void> {
  if (scanning.value) return;
  scanning.value = true;
  try {
    ecus.value = (await api.toyotaScanDtc()).ecus;
    selected.value = [];
    results.value = null;
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    scanning.value = false;
  }
}

async function clearSelected(): Promise<void> {
  if (clearing.value) return;
  clearing.value = true;
  try {
    results.value = (await api.toyotaClearDtc(selected.value)).results;
    confirmOpen.value = false;
    const failed = results.value.filter((r) => !r.ok).length;
    toast(failed ? `${failed} 个 ECU 清除失败` : "已清除，请重新扫描确认", failed ? "warn" : "ok");
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    clearing.value = false;
  }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-3">
      <Button variant="accent" :disabled="scanning" @click="scan">
        <Loader2 v-if="scanning" class="animate-spin" />
        <ScanSearch v-else />
        扫描故障码
      </Button>
      <Button variant="danger" :disabled="!canClear" @click="confirmOpen = true">
        <Eraser />
        清除所选{{ selected.length ? `（${selected.length}）` : "" }}
      </Button>
      <span v-if="scanning" class="text-[13px] text-sl-text-2">正在逐个 ECU 读取，可能需要 30 秒以上…</span>
    </div>

    <p v-if="ecus && !ecus.length" class="sl-card px-5 py-6 text-center text-[13px] text-sl-text-3">
      没有 ECU 响应
    </p>

    <section v-for="e in ecus ?? []" :key="e.ecu" class="sl-card px-5 py-4">
      <div class="flex items-center gap-3">
        <Checkbox
          :model-value="selected.includes(e.ecu)"
          :aria-label="`选择 ${e.name}`"
          @update:model-value="setSelected(e.ecu, $event === true)"
        />
        <h2 class="text-sm font-semibold text-sl-text-1">{{ e.name }}</h2>
        <span class="text-[12px] text-sl-text-3">{{ e.ecu }}</span>
        <Badge :kind="e.dtcs.length ? 'warn' : 'accent'">{{ e.dtcs.length ? `${e.dtcs.length} 个故障码` : "无故障码" }}</Badge>
      </div>
      <ul v-if="e.dtcs.length" class="mt-2 divide-y divide-sl-border/70">
        <li v-for="d in e.dtcs" :key="d.code + d.status" class="flex flex-wrap items-baseline gap-x-3 py-2 text-[13px]">
          <span class="font-mono font-semibold text-sl-text-1">{{ d.code }}</span>
          <Badge kind="muted">{{ d.status }}</Badge>
          <span class="text-sl-text-2">{{ d.description || "（无描述）" }}</span>
        </li>
      </ul>
    </section>

    <section v-if="results" class="sl-card px-5 py-4">
      <h2 class="mb-1 text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">清除结果</h2>
      <ul class="divide-y divide-sl-border/70">
        <li v-for="r in results" :key="r.ecu" class="flex flex-wrap items-center gap-3 py-2 text-[13px]">
          <span class="text-sl-text-1">{{ nameOf(r.ecu) || r.ecu }}</span>
          <Badge :kind="r.ok ? 'accent' : 'danger'">{{ r.ok ? "已清除" : "失败" }}</Badge>
          <span v-if="r.error" class="text-sl-danger">{{ r.error }}</span>
        </li>
      </ul>
      <p class="mt-2 text-[12px] text-sl-text-3">请重新扫描确认故障码是否已消除。</p>
    </section>

    <ConfirmDialog
      v-model:open="confirmOpen"
      title="确认清除故障码"
      confirm-text="清除"
      :busy="clearing"
      danger
      @confirm="clearSelected"
    >
      <p>将清除以下 ECU 的故障码及冻结帧数据，此操作不可撤销：</p>
      <ul class="list-disc pl-5 text-sl-text-1">
        <li v-for="ecu in selected" :key="ecu">{{ nameOf(ecu) || ecu }}</li>
      </ul>
    </ConfirmDialog>
  </div>
</template>
