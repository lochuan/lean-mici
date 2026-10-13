<script setup lang="ts">
/** 备份页：写入前自动生成的备份列表，恢复要二次确认。 */
import { ref, watch } from "vue";
import { Loader2, RefreshCw, Undo2 } from "lucide-vue-next";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { ToyotaBackup } from "@/lib/schema";
import { errorText } from "@/lib/toyota";
import Button from "../ui/Button.vue";
import ConfirmDialog from "./ConfirmDialog.vue";

const props = defineProps<{ active: boolean }>();

const backups = ref<ToyotaBackup[] | null>(null);
const loading = ref(false);
const target = ref<ToyotaBackup | null>(null);
const restoring = ref(false);
const restoreOpen = ref(false);

async function load(): Promise<void> {
  if (loading.value) return;
  loading.value = true;
  try {
    backups.value = (await api.toyotaBackups()).backups;
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    loading.value = false;
  }
}

// 每次切到本页都刷新：写入定制项会产生新备份
watch(() => props.active, (a) => { if (a) void load(); }, { immediate: true });

const timeText = (unixS: number) => new Date(unixS * 1000).toLocaleString();

function ask(b: ToyotaBackup): void {
  target.value = b;
  restoreOpen.value = true;
}

async function restore(): Promise<void> {
  const b = target.value;
  if (!b || restoring.value) return;
  restoring.value = true;
  try {
    const r = await api.toyotaRestore(b.id);
    toast(r.verified ? "已恢复，回读校验通过" : "已恢复，但回读校验未通过", r.verified ? "ok" : "warn");
    restoreOpen.value = false;
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    restoring.value = false;
  }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div>
      <Button :disabled="loading" @click="load">
        <Loader2 v-if="loading" class="animate-spin" />
        <RefreshCw v-else />
        刷新
      </Button>
    </div>

    <p v-if="backups && !backups.length" class="sl-card px-5 py-6 text-center text-[13px] text-sl-text-3">
      还没有备份；每次写入定制项前会自动生成
    </p>

    <section v-for="b in backups ?? []" :key="b.id" class="sl-card flex flex-wrap items-start justify-between gap-3 px-5 py-4">
      <div class="min-w-0 flex-1">
        <div class="text-sm text-sl-text-1">{{ b.name }}</div>
        <div class="text-[12px] text-sl-text-3">{{ timeText(b.time) }} · {{ b.ecu }} · 本地 ID {{ b.localId }}</div>
        <div class="mt-1 break-all font-mono text-[12px] text-sl-text-2">{{ b.data }}</div>
      </div>
      <Button size="sm" variant="danger" @click="ask(b)"><Undo2 />恢复</Button>
    </section>

    <ConfirmDialog
      v-model:open="restoreOpen"
      title="确认恢复备份"
      confirm-text="恢复"
      :busy="restoring"
      danger
      @confirm="restore"
    >
      <template v-if="target">
        <div class="text-sl-text-1">{{ target.name }}（{{ timeText(target.time) }}）</div>
        <p>将把 {{ target.ecu }} 的该定制项写回备份时的数据：</p>
        <div class="break-all font-mono text-sl-text-1">{{ target.data }}</div>
      </template>
    </ConfirmDialog>
  </div>
</template>
