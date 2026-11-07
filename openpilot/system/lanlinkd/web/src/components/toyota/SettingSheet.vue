<script setup lang="ts">
/** 设置项底部弹层：选值 → 确认「旧值 → 新值」→ 备份 / 写入 / 校验 → toast「已保存」带「撤销」。
 *
 * 阶段 pick（单选列表 / 数值滑块，折叠的技术详情）→ confirm → running → 关闭。
 * 后端一次请求完成备份、写入、回读校验，看不到中间进度，所以 running 里的三步是按时间推进的提示，
 * 最后一步停在转圈，等响应回来才算完成。
 */
import { computed, onUnmounted, ref, watch } from "vue";
import { Check, Circle, Loader2 } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { ToyotaSetting } from "@/lib/schema";
import { toast } from "@/lib/store";
import { ecuProtocolText, errorText, previewRaw, restoreToast, settingValueLabel } from "@/lib/toyota";
import { applyWriteResult, invalidateCustomizationScan, toyotaData } from "@/lib/toyotaData";
import { requireActive } from "@/lib/toyotaSession";
import { cn } from "@/lib/utils";
import Button from "../ui/Button.vue";
import Sheet from "../ui/Sheet.vue";
import HexDiff from "./HexDiff.vue";

const props = defineProps<{ setting: ToyotaSetting | null }>();
const emit = defineEmits<{ close: [] }>();

const STEPS = ["备份中", "写入中", "校验中"];

const stage = ref<"pick" | "confirm" | "running">("pick");
const picked = ref(0);
const activeStep = ref(0);
let stepTimer: ReturnType<typeof setInterval> | undefined;

const entry = computed(() => (props.setting ? toyotaData.customization.values[props.setting.key] : undefined));
const ecu = computed(() => toyotaData.catalog?.ecus.find((e) => e.id === props.setting?.ecu));
const current = computed(() => entry.value?.value);
const label = (v: number | undefined) => (props.setting ? settingValueLabel(props.setting, v) : "");

// 每次打开重置到选值阶段；数值滑块从当前值开始
watch(() => props.setting, (s) => {
  stage.value = "pick";
  picked.value = s ? (toyotaData.customization.values[s.key]?.value ?? s.numeric?.min ?? 0) : 0;
});
onUnmounted(() => clearInterval(stepTimer));

function pick(value: number): void {
  picked.value = value;
  if (value === current.value) emit("close");
  else stage.value = "confirm";
}

const protocolText = computed(() => (ecu.value ? ecuProtocolText(ecu.value.protocol) : ""));
const addressText = computed(() => `0x${ecu.value?.tx}${ecu.value?.sub ? ` · 子地址 0x${ecu.value.sub}` : ""}`);
const afterRaw = computed(() => {
  const s = props.setting;
  return s && entry.value ? previewRaw(entry.value.raw, s.byteIndex, s.mask, picked.value) : "";
});

async function apply(): Promise<void> {
  const s = props.setting;
  const before = entry.value;
  if (!s || !before || !requireActive()) return;
  stage.value = "running";
  activeStep.value = 0;
  stepTimer = setInterval(() => { activeStep.value = Math.min(activeStep.value + 1, STEPS.length - 1); }, 700);
  try {
    const r = await api.toyotaWrite(s.key, picked.value, before.raw);
    applyWriteResult(s, r.after);
    toast(r.verified ? "已保存" : "已写入，但回读校验未通过", r.verified ? "ok" : "warn", {
      label: "撤销",
      run: () => void undo(s, r.backupId),
    });
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    clearInterval(stepTimer);
    emit("close");
  }
}

async function undo(s: ToyotaSetting, backupId: string): Promise<void> {
  if (!requireActive()) return;
  try {
    const r = await api.toyotaRestore(backupId);
    applyWriteResult(s, r.after);
    const restored = restoreToast(s.name, settingValueLabel(s, r.value), r.verified);
    toast(restored.message, restored.tone);
  } catch (e) {
    // 撤销失败时车上的值不确定，让定制页下次重新扫描
    invalidateCustomizationScan();
    toast(errorText(e), "error");
  }
}
</script>

<template>
  <Sheet
    :open="setting !== null"
    :title="stage === 'pick' ? (setting?.name ?? '') : stage === 'confirm' ? '确认修改' : '正在修改'"
    :description="stage === 'pick' ? ecu?.name : stage === 'confirm' ? setting?.name : undefined"
    :persistent="stage === 'running'"
    @update:open="(v) => { if (!v) emit('close'); }"
  >
    <template v-if="setting">
      <!-- 选值 -->
      <template v-if="stage === 'pick'">
        <div v-if="setting.numeric" class="mt-4">
          <div class="my-3 flex justify-center">
            <i class="sl-tabular rounded-lg bg-sl-accent-muted px-4 py-1.5 text-[20px] font-semibold not-italic text-sl-accent">{{ picked }}</i>
          </div>
          <input v-model.number="picked" type="range" :min="setting.numeric.min" :max="setting.numeric.max" class="w-full accent-sl-accent" />
          <div class="flex justify-between text-[12px] text-sl-text-2"><span>{{ setting.numeric.min }}</span><span>{{ setting.numeric.max }}</span></div>
          <Button variant="accent" class="mt-3 w-full" @click="pick(picked)">选择此值</Button>
        </div>
        <template v-else>
          <button
            v-for="o in setting.options"
            :key="o.value"
            type="button"
            :class="cn('mt-2 flex w-full items-center gap-3 rounded-[10px] border bg-sl-surface-2 px-3 py-3 text-left', o.value === current ? 'border-sl-accent' : 'border-sl-border')"
            @click="pick(o.value)"
          >
            <Check v-if="o.value === current" class="size-[18px] shrink-0 text-sl-accent" />
            <Circle v-else class="size-[18px] shrink-0 text-sl-border-strong" />
            <span class="text-[15px] text-sl-text-1">{{ o.label }}</span>
            <span v-if="o.value === current" class="ml-auto text-[12px] text-sl-accent">当前</span>
          </button>
        </template>
      </template>

      <!-- 确认 -->
      <template v-else-if="stage === 'confirm'">
        <div class="my-4 flex flex-wrap items-center justify-center gap-2.5 text-[17px]">
          <i class="rounded-lg bg-sl-surface-3 px-3 py-1.5 not-italic text-sl-text-1">{{ label(current) }}</i>
          <span class="text-sl-text-3">→</span>
          <i class="rounded-lg bg-sl-accent-muted px-3 py-1.5 font-semibold not-italic text-sl-accent">{{ label(picked) }}</i>
        </div>
        <p class="text-center text-[13px] text-sl-text-2">会先自动备份，可随时撤销。</p>
        <p class="mt-1 text-center text-[13px] text-sl-warn">请确认车辆处于 IG-ON（未进入 Ready）</p>
        <div class="mt-4 flex gap-2.5">
          <Button class="flex-1" @click="stage = 'pick'">取消</Button>
          <Button variant="accent" class="flex-1" @click="apply">确认修改</Button>
        </div>
      </template>

      <!-- 写入中 -->
      <ul v-else class="my-3 flex flex-col">
        <li
          v-for="(t, i) in STEPS"
          :key="t"
          :class="cn('flex items-center gap-2.5 py-2 text-[15px]', i < activeStep ? 'text-sl-accent' : i === activeStep ? 'text-sl-text-1' : 'text-sl-text-3')"
        >
          <Check v-if="i < activeStep" class="size-4" />
          <Loader2 v-else-if="i === activeStep" class="size-4 animate-spin" />
          <Circle v-else class="size-4" />
          {{ t }}
        </li>
      </ul>

      <!-- 技术详情（写入中不显示） -->
      <details v-if="stage !== 'running' && ecu" class="sl-card mt-4 px-3.5 py-3">
        <summary class="cursor-pointer text-[13px] text-sl-text-2">技术详情</summary>
        <table class="mt-2 w-full text-[12px]">
          <tbody class="[&_td]:border-b [&_td]:border-sl-border [&_td]:py-1 [&_td:first-child]:w-[36%] [&_td:first-child]:text-sl-text-3 [&_td:last-child]:font-mono [&_td:last-child]:text-sl-text-1">
            <tr><td>ECU</td><td class="!font-sans">{{ ecu.name }}</td></tr>
            <tr><td>协议</td><td class="!font-sans">{{ protocolText }}</td></tr>
            <tr><td>请求地址</td><td>{{ addressText }}</td></tr>
            <tr><td>{{ setting.did ? "DID" : "本地 ID" }}</td><td>{{ setting.did ?? setting.localId }}</td></tr>
            <tr><td>字节 / 掩码</td><td>byte {{ setting.byteIndex }} · mask 0x{{ setting.mask }}</td></tr>
            <tr><td>当前原始值</td><td class="break-all">{{ entry?.raw }}</td></tr>
          </tbody>
        </table>
        <div v-if="stage === 'confirm' && entry" class="mt-3"><HexDiff :before="entry.raw" :after="afterRaw" /></div>
      </details>
    </template>
  </Sheet>
</template>
