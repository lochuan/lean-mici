<script setup lang="ts">
/** 提示条。写入失败必须让用户看见——乐观更新会让开关先动，
 *  如果失败时无声回滚，用户会以为设置生效了。 */
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-vue-next";
import { store } from "@/lib/store";
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
    <div class="pointer-events-none fixed bottom-6 right-6 z-[70] flex flex-col items-end gap-2">
      <TransitionGroup
        enter-active-class="transition duration-200 ease-out"
        leave-active-class="transition duration-150 ease-in"
        enter-from-class="translate-y-2 opacity-0"
        leave-to-class="translate-x-4 opacity-0"
        move-class="transition-transform duration-200"
      >
        <div
          v-for="t in store.toasts"
          :key="t.id"
          :class="
            cn(
              'pointer-events-auto flex max-w-[420px] items-start gap-2.5 rounded-lg border border-sl-border',
              'bg-sl-surface-2 px-4 py-3 text-[13px] text-sl-text-1 shadow-xl shadow-black/50 ring-1 ring-inset',
              TONE[t.kind],
            )
          "
        >
          <component :is="ICON[t.kind]" class="mt-0.5 size-4 shrink-0" />
          <span class="text-sl-text-1">{{ t.text }}</span>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>
