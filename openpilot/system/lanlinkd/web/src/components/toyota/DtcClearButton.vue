<script setup lang="ts">
/** 「清除故障码」按钮 + 确认框：故障码页清除全部、单个 ECU 页清除此 ECU 共用。 */
import { computed, ref } from "vue";
import { Eraser } from "lucide-vue-next";
import { toast } from "@/lib/store";
import { errorText } from "@/lib/toyota";
import { clearDtcs, toyotaData } from "@/lib/toyotaData";
import { requireActive } from "@/lib/toyotaSession";
import Button from "../ui/Button.vue";
import ConfirmDialog from "./ConfirmDialog.vue";

const props = defineProps<{ label: string; ecuIds: string[] }>();

const open = ref(false);
const busy = ref(false);

const codeCount = computed(() =>
  toyotaData.dtc.scans.filter((s) => props.ecuIds.includes(s.ecu)).reduce((n, s) => n + s.dtcs.length, 0),
);

async function clear(): Promise<void> {
  if (busy.value || !requireActive()) return;
  busy.value = true;
  try {
    const results = await clearDtcs(props.ecuIds);
    const failed = results.filter((r) => !r.ok);
    if (failed.length) toast(`${failed.length} 个 ECU 清除失败：${failed[0].error ?? "ECU 拒绝"}`, "error");
    else toast("已清除。若故障仍在，下次扫描会再次出现");
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    busy.value = false;
    open.value = false;
  }
}
</script>

<template>
  <Button variant="danger" :disabled="!codeCount" @click="open = true"><Eraser />{{ label }}</Button>

  <ConfirmDialog v-model:open="open" title="清除故障码" confirm-text="确认清除" danger :busy="busy" @confirm="clear">
    <p>将清除 {{ ecuIds.length }} 个 ECU 的 {{ codeCount }} 条故障码。</p>
    <p>清除只删除记录，<b class="text-sl-text-1">不会修复故障</b>；若故障仍在，会再次出现。</p>
    <p class="text-sl-warn">请确认车辆处于 IG-ON（未进入 Ready）</p>
  </ConfirmDialog>
</template>
