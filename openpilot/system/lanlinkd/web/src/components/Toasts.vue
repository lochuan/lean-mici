<script setup lang="ts">
/** 提示条。写入失败必须让用户看见——乐观更新会让开关先动，
 *  如果失败时无声回滚，用户会以为设置生效了。 */
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-vue-next";
import { dismissToast, store } from "@/lib/store";
import { cn } from "@/lib/utils";

const ICON = { ok: CheckCircle2, warn: AlertTriangle, error: XCircle };
const TONE = {
  ok: "ring-sl-accent/30 text-sl-accent",
  warn: "ring-sl-warn/30 text-sl-warn",
  error: "ring-sl-danger/30 text-sl-danger",
};
</script>

<template>
  <Teleport to="body">
    <div class="pointer-events-none fixed inset-x-4 bottom-6 z-[70] flex flex-col items-center gap-2 sm:left-auto sm:right-6 sm:items-end">
      <TransitionGroup
        enter-active-class="transition-[transform,opacity] duration-200 ease-[cubic-bezier(0.23,1,0.32,1)]"
        leave-active-class="transition-[transform,opacity] duration-150 ease-[cubic-bezier(0.23,1,0.32,1)]"
        enter-from-class="translate-y-2 opacity-0 motion-reduce:translate-y-0"
        leave-to-class="translate-y-2 opacity-0 motion-reduce:translate-y-0"
        move-class="transition-transform duration-200 ease-[cubic-bezier(0.23,1,0.32,1)] motion-reduce:transition-none"
      >
        <div
          v-for="t in store.toasts"
          :key="t.id"
          :role="t.kind === 'error' ? 'alert' : 'status'"
          aria-atomic="true"
          :class="
            cn(
              'pointer-events-auto flex max-w-[420px] items-start gap-2.5 rounded-lg border border-sl-border',
              'bg-sl-surface px-4 py-3 text-sm text-sl-text-1 shadow-lg shadow-black/50 ring-1 ring-inset',
              TONE[t.kind],
            )
          "
        >
          <component :is="ICON[t.kind]" class="mt-0.5 size-4 shrink-0" />
          <span class="text-sl-text-1">{{ t.text }}</span>
          <button
            v-if="t.action"
            type="button"
            class="sl-press -my-1 -mr-2 ml-1 shrink-0 rounded-md px-2 py-1 text-sm font-semibold text-sl-accent hover:bg-sl-accent/10"
            @click="t.action.run(); dismissToast(t.id)"
          >
            {{ t.action.label }}
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
