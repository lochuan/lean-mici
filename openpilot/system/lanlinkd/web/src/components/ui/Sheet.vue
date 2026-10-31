<script setup lang="ts">
/** 底部弹层（手机上的 bottom sheet）：reka-ui 无头原语 + sl 主题。
 *
 * 写法与 Dialog.vue 一致：v-model:open 控制开关，title 必填，内容走默认插槽，
 * Portal 渲染到 body。persistent 时点遮罩 / ESC / 右上角 X 都不能关闭，
 * 用于写入进行中——此时关掉弹层看不到结果，还会误以为没改成。
 */
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from "reka-ui";
import { X } from "lucide-vue-next";

const props = defineProps<{
  open: boolean;
  title: string;
  /** 标题下的小字（如设置项所属 ECU） */
  description?: string;
  persistent?: boolean;
}>();

const emit = defineEmits<{ "update:open": [boolean] }>();
</script>

<template>
  <DialogRoot :open="props.open" @update:open="(v: boolean) => emit('update:open', v)">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-black/70" />
      <DialogContent
        class="fixed inset-x-0 bottom-0 z-50 mx-auto max-h-[86vh] w-full max-w-[480px] overflow-y-auto rounded-t-2xl border border-b-0 border-sl-border-strong bg-sl-surface px-4 pb-[calc(1.25rem+env(safe-area-inset-bottom))] pt-4 shadow-lg shadow-black/50 focus:outline-none data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom data-[state=closed]:animate-out data-[state=closed]:slide-out-to-bottom"
        :aria-describedby="props.description ? undefined : ''"
        @escape-key-down="props.persistent && $event.preventDefault()"
        @interact-outside="props.persistent && $event.preventDefault()"
      >
        <DialogTitle class="pr-8 text-[17px] font-semibold text-sl-text-1">{{ props.title }}</DialogTitle>
        <DialogDescription v-if="props.description" class="text-[12px] text-sl-text-2">
          {{ props.description }}
        </DialogDescription>
        <slot />
        <DialogClose
          v-if="!props.persistent"
          class="absolute right-3 top-3 grid size-8 place-items-center rounded-md text-sl-text-3 opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="关闭"
        >
          <X class="size-4" />
        </DialogClose>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
