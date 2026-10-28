<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Button from "./ui/Button.vue";
import type { DBCActivationStatus } from "@/lib/schema";

const props = defineProps<{ status: DBCActivationStatus | null; busy: boolean; error: string; platform?: string }>();
const emit = defineEmits<{ activate: [code: string] }>();
const code = ref("");
const message = computed(() => {
  if (props.status?.cleanup_pending) return "授权已吊销，停车后完成清理；完成前无法选择此车型。";
  if (props.status?.revoked) return "授权已吊销，资源已清理，可停车后重新激活。";
  if (props.status?.issue === "device_mismatch") return "绑定设备不符：当前授权属于其他车机，请使用本机的激活码。";
  if (props.status?.issue === "serial_unavailable") return "无法读取车机序列号，暂不能使用此授权。";
  if (props.status?.issue === "source_unavailable") return "运行版本信息不可用，DBC 暂不能就绪；授权检查和吊销清理仍在运行。";
  if (props.status?.ready) return "已激活，DBC 已就绪";
  if (props.status?.activated) return "已激活，DBC 尚未就绪；停车联网后自动重试下载。";
  return "此车型需要绑定当前车机并下载专用 DBC。";
});
watch(() => props.status?.ready, (ready) => { if (ready) code.value = ""; });

function submit(): void {
  if (code.value.trim() && props.status?.can_activate && !props.busy) emit("activate", code.value.trim());
}
</script>

<template>
  <div v-if="!platform || platform.startsWith('TOYOTA_')" class="border-t border-sl-border p-4">
    <h3 class="text-sm font-medium text-sl-text-1">Sienna Patched LTA</h3>
    <p class="mt-1 text-[13px] text-sl-text-2" role="status" aria-live="polite">{{ message }}</p>
    <form class="mt-3 flex flex-wrap items-end gap-2" @submit.prevent="submit">
      <div class="min-w-0 flex-1">
        <label for="sienna-activation-code" class="mb-1 block text-xs text-sl-text-2">激活码</label>
        <input
          id="sienna-activation-code" v-model="code" name="activation-code" type="password"
          autocomplete="off" :maxlength="256" :disabled="busy || !status?.can_activate"
          aria-describedby="sienna-activation-help"
          class="h-11 w-full rounded-lg bg-sl-bg px-3 text-sm text-sl-text-1 ring-1 ring-inset ring-sl-border focus:outline-none focus:ring-sl-accent disabled:opacity-50"
        />
      </div>
      <Button type="submit" :disabled="busy || !status?.can_activate || !code.trim()">
        {{ busy ? "正在激活和下载…" : "激活并下载" }}
      </Button>
    </form>
    <p id="sienna-activation-help" class="mt-2 text-xs text-sl-text-3">仅停车时可激活；激活码绑定车机硬件序列号。</p>
    <p v-if="error || status?.error" role="alert" class="mt-2 text-[13px] text-sl-text-1">{{ error || status?.error }}</p>
  </div>
</template>
