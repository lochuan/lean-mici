<script setup lang="ts">
/** 确认框：标题 + 说明插槽 + 取消 / 确认。busy 时两个按钮都置灰，防止重复提交。 */
import { Loader2 } from "lucide-vue-next";
import Button from "../ui/Button.vue";
import Dialog from "../ui/Dialog.vue";

defineProps<{
  open: boolean;
  title: string;
  confirmText: string;
  busy?: boolean;
  danger?: boolean;
}>();
const emit = defineEmits<{ "update:open": [boolean]; confirm: [] }>();
</script>

<template>
  <Dialog :open="open" :title="title" @update:open="emit('update:open', $event)">
    <div class="mt-3 flex flex-col gap-3 text-[13px] leading-relaxed text-sl-text-2">
      <slot />
    </div>
    <div class="mt-5 flex justify-end gap-2">
      <Button variant="ghost" :disabled="busy" @click="emit('update:open', false)">
        取消
      </Button>
      <Button :variant="danger ? 'danger' : 'accent'" :disabled="busy" @click="emit('confirm')">
        <Loader2 v-if="busy" class="animate-spin" />
        {{ confirmText }}
      </Button>
    </div>
  </Dialog>
</template>
