<script setup lang="ts">
/** 模态对话框：reka-ui 无头原语 + sl 主题（shadcn-vue 的 Dialog 形态）。
 *
 * 用法：v-model:open 控制开关，title 必填，内容走默认插槽。
 * Portal 渲染到 body：父组件（如 2s 轮询的 WifiPanel）重渲染不影响弹层，
 * ESC / 点遮罩关闭由 DialogRoot 处理，焦点圈定与还原免实现。
 */
import {
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from "reka-ui";

const props = defineProps<{
  open: boolean;
  title: string;
}>();

const emit = defineEmits<{ "update:open": [boolean] }>();
</script>

<template>
  <DialogRoot
    :open="props.open"
    @update:open="(v: boolean) => emit('update:open', v)"
  >
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-black/60" />
      <DialogContent
        class="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2.5rem)] max-w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-sl-border bg-sl-surface p-5 shadow-lg shadow-black/40 focus:outline-none"
      >
        <DialogTitle class="text-[15px] font-semibold text-sl-text-1">
          {{ title }}
        </DialogTitle>
        <slot />
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
