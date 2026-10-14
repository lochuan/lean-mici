<script setup lang="ts">
/** 备份记录：每次修改前自动生成的备份，可恢复（恢复前后端会另存当前值）。
 *  列表不需要诊断模式；恢复要写 ECU，需要。 */
import { onMounted, ref } from "vue";
import { Loader2 } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { ToyotaBackup } from "@/lib/schema";
import { toast } from "@/lib/store";
import { errorText, restoreToast } from "@/lib/toyota";
import { invalidateCustomizationScan } from "@/lib/toyotaData";
import { requireActive } from "@/lib/toyotaSession";
import Badge from "../ui/Badge.vue";
import Button from "../ui/Button.vue";

const backups = ref<ToyotaBackup[] | null>(null);
const restoringId = ref("");

const timeText = (unixS: number) => new Date(unixS * 1000).toLocaleString();

async function load(): Promise<void> {
  try {
    backups.value = (await api.toyotaBackups()).backups;
  } catch (e) {
    toast(errorText(e), "error");
  }
}

async function restore(b: ToyotaBackup): Promise<void> {
  if (restoringId.value || !requireActive()) return;
  restoringId.value = b.id;
  try {
    const r = await api.toyotaRestore(b.id);
    invalidateCustomizationScan();
    const restored = restoreToast(b.name, b.before.label, r.verified);
    toast(restored.message, restored.tone);
    await load(); // 恢复前另存了一份当前值，列表会多一条
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    restoringId.value = "";
  }
}

onMounted(load);
</script>

<template>
  <div v-if="!backups" class="grid place-items-center py-10"><Loader2 class="size-5 animate-spin text-sl-text-3" /></div>
  <div v-else-if="!backups.length" class="sl-card p-5 text-center text-[13px] text-sl-text-2">
    还没有备份。<br />每次修改定制项前都会自动备份旧值。
  </div>
  <div v-else class="sl-card divide-y divide-sl-border overflow-hidden">
    <div v-for="b in backups" :key="b.id" class="flex items-center gap-3 px-3.5 py-3">
      <div class="min-w-0 flex-1">
        <div class="text-[15px] text-sl-text-1">{{ b.name }}</div>
        <div class="text-[12px] text-sl-text-3">
          {{ timeText(b.time) }} · 旧值 {{ b.before.label }}<template v-if="b.after"> → 新值 {{ b.after.label }}</template>
        </div>
      </div>
      <Badge v-if="b.restored">已恢复</Badge>
      <Button v-else size="sm" :disabled="restoringId !== ''" @click="restore(b)">
        <Loader2 v-if="restoringId === b.id" class="animate-spin" />恢复
      </Button>
    </div>
  </div>
</template>
