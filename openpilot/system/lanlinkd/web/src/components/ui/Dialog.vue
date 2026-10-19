<script setup lang="ts">
/** 模态对话框（shadcn-vue Dialog 形态）：reka-ui 无头原语 + sl 主题。
 *
 * 用法：v-model:open 控制开关，title 必填，内容走默认插槽。
 * Portal 渲染到 body：父组件（如 2s 轮询的 WifiPanel）重渲染不影响弹层，
 * ESC / 点遮罩 / 右上角 X 关闭由 DialogRoot/DialogClose 处理，
 * 焦点圈定与还原免实现。
 */
import {
  DialogClose,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from "reka-ui";
import { X } from "lucide-vue-next";

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
      <DialogOverlay class="fixed inset-0 z-50 bg-black/80" />
      <DialogContent
        class="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2.5rem)] max-w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-sl-border bg-sl-surface p-5 shadow-lg shadow-black/40 focus:outline-none"
      >
        <DialogTitle class="text-[15px] font-semibold text-sl-text-1">
          {{ title }}
        </DialogTitle>
        <slot />
        <DialogClose
          class="absolute right-4 top-4 grid size-7 place-items-center rounded-md text-sl-text-3 opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="关闭"
        >
          <X class="size-4" />
        </DialogClose>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
