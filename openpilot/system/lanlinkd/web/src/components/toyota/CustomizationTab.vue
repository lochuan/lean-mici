<script setup lang="ts">
/** 定制页：扫描 → 按组折叠 → 选新值 → 预览改前/改后 → 确认写入 → 结果。
 *  写入带上预览返回的 before，设备端发现与实时回读不一致会 409，防止预览后被改过。 */
import { computed, ref } from "vue";
import { Loader2, ScanSearch } from "lucide-vue-next";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { ToyotaCustomizationItem, ToyotaPreview, ToyotaScanEcu, ToyotaWriteResult } from "@/lib/schema";
import { errorText, groupCustomizations, scanEcuText, scanSummary } from "@/lib/toyota";
import Accordion from "../ui/Accordion.vue";
import Badge from "../ui/Badge.vue";
import Button from "../ui/Button.vue";
import Select from "../ui/Select.vue";
import ConfirmDialog from "./ConfirmDialog.vue";
import HexDiff from "./HexDiff.vue";

const READONLY_REASON = "数据库读写 ID 不一致，已禁止写入";

const items = ref<ToyotaCustomizationItem[] | null>(null);
const ecus = ref<ToyotaScanEcu[]>([]);
const scanning = ref(false);
const previewing = ref<number | null>(null);

const groups = computed(() => groupCustomizations(items.value ?? []));
const accordionEntries = computed(() =>
  groups.value.map((g) => ({ id: g.group, label: g.group, count: g.items.length })),
);
const itemsOf = (group: string) => groups.value.find((g) => g.group === group)?.items ?? [];

async function scan(): Promise<void> {
  if (scanning.value) return;
  scanning.value = true;
  try {
    const scanned = await api.toyotaScanCustomizations();
    items.value = scanned.items;
    ecus.value = scanned.ecus;
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    scanning.value = false;
  }
}

const optionChoices = (it: ToyotaCustomizationItem) =>
  it.options.map((o) => ({ value: o.index, label: o.title }));
const optionTitle = (it: ToyotaCustomizationItem, index: number | null) =>
  index === null ? "未匹配任何选项" : (it.options.find((o) => o.index === index)?.title ?? `选项 ${index}`);

// ---- 预览 / 写入 ----
const target = ref<{ item: ToyotaCustomizationItem; option: number; preview: ToyotaPreview } | null>(null);
const writing = ref(false);
const result = ref<ToyotaWriteResult | null>(null);
const dialogOpen = computed({
  get: () => target.value !== null,
  set: (v) => {
    if (!v && !writing.value) {
      target.value = null;
      result.value = null;
    }
  },
});

async function pick(it: ToyotaCustomizationItem, value: string): Promise<void> {
  const option = Number(value);
  if (option === it.current || previewing.value !== null) return;
  previewing.value = it.uid;
  try {
    const preview = await api.toyotaPreview(it.uid, option);
    result.value = null;
    target.value = { item: it, option, preview };
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    previewing.value = null;
  }
}

async function confirmWrite(): Promise<void> {
  const t = target.value;
  if (!t || writing.value) return;
  writing.value = true;
  try {
    const r = await api.toyotaWrite(t.item.uid, t.option, t.preview.before);
    result.value = r;
    t.item.current = r.current;
    toast(r.verified ? "写入成功，回读校验通过" : "已写入，但回读校验未通过", r.verified ? "ok" : "warn");
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    writing.value = false;
  }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-3">
      <Button variant="accent" :disabled="scanning" @click="scan">
        <Loader2 v-if="scanning" class="animate-spin" />
        <ScanSearch v-else />
        读取车辆定制项
      </Button>
      <span v-if="scanning" class="text-[13px] text-sl-text-2">正在逐个 ECU 读取，可能需要 30 秒以上…</span>
      <span v-else-if="items" class="text-[13px] text-sl-text-3">共 {{ items.length }} 项 · {{ scanSummary(ecus) }}</span>
    </div>

    <div v-if="items && !items.length" class="sl-card flex flex-col gap-3 px-5 py-5 text-[13px] text-sl-text-3">
      <p class="text-center">没有读到任何定制项，各 ECU 的结果：</p>
      <ul class="flex flex-col gap-1">
        <li v-for="e in ecus" :key="e.ecu" class="flex flex-wrap gap-x-3">
          <span class="text-sl-text-2">{{ e.ecu }}</span>
          <span>{{ scanEcuText(e) }}</span>
        </li>
      </ul>
      <p class="text-center">若 ECU 普遍拒绝或不应答 A8 03，可试试只通电不启动（IG-ON）再读取</p>
    </div>

    <Accordion v-if="accordionEntries.length" :items="accordionEntries">
      <template #default="{ item: entry }">
        <div class="divide-y divide-sl-border/70">
          <div v-for="it in itemsOf(entry.id)" :key="it.uid" class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 text-sm text-sl-text-1">
                {{ it.name }}
                <Badge v-if="!it.writable" kind="warn">只读</Badge>
              </div>
              <div class="text-[12px] text-sl-text-3">{{ it.ecu }} · 当前：{{ optionTitle(it, it.current) }}</div>
              <div v-if="!it.writable" class="text-[12px] text-sl-warn">{{ READONLY_REASON }}</div>
            </div>
            <Select
              :model-value="it.current === null ? '' : String(it.current)"
              :options="optionChoices(it)"
              :disabled="!it.writable"
              :pending="previewing === it.uid"
              :aria-label="`${it.name} 的新选项`"
              @commit="pick(it, $event)"
            />
          </div>
        </div>
      </template>
    </Accordion>

    <ConfirmDialog
      v-model:open="dialogOpen"
      :title="result ? '写入结果' : '确认写入'"
      confirm-text="确认写入"
      :busy="writing"
      :done="result !== null"
      danger
      @confirm="confirmWrite"
    >
      <template v-if="target">
        <div class="text-sl-text-1">{{ target.item.name }}</div>
        <div>{{ target.item.ecu }}</div>
        <div>
          改为：<span class="text-sl-text-1">{{ optionTitle(target.item, target.option) }}</span>
        </div>
        <HexDiff :before="target.preview.before" :after="target.preview.after" />
        <template v-if="result">
          <div :class="result.verified ? 'text-sl-accent' : 'text-sl-danger'">
            {{ result.verified ? "回读校验通过" : "回读校验未通过，请检查后从备份恢复" }}
          </div>
          <div>备份 ID：<span class="font-mono text-sl-text-1">{{ result.backupId }}</span></div>
        </template>
        <p v-else>写入前会自动备份，写入后回读校验。</p>
      </template>
    </ConfirmDialog>
  </div>
</template>
