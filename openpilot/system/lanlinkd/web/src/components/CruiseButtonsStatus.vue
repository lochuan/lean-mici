<script setup lang="ts">
/** 巡航按钮控制设置区的状态卡片(照 AvoidanceCalibration.vue 的结构与轮询模式)。
 *  数据来自 lanlinkd 的 /api/cruise-buttons(照 /api/avoidance 模式的
 *  cruiseButtonsDebug 快照线程)。BT 未连接时提示硬件前提,其余字段
 *  只在 enabled 时有意义。 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "@/lib/api";
import { boolValue } from "@/lib/store";
import Badge from "./ui/Badge.vue";

interface CruiseButtonsSnapshot {
  stale?: boolean;
  enabled?: boolean;
  btConnected?: boolean;
  btState?: number; // 0=idle 1=executing 2=fault
  ceilingKph?: number;
  targetKph?: number;
  setSpeedKph?: number;
  vEgoKph?: number;
  quantumKph?: number;
  sccActive?: boolean;
  leadPresent?: boolean;
  leadSpeedKph?: number;
  standstill?: boolean;
  lastButton?: number; // 0=RES+ 1=RES- 255=none
  lastEchoOurs?: boolean;
  unexplained?: number;
  frozen?: boolean;
}

const snap = ref<CruiseButtonsSnapshot | null>(null);
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    snap.value = await api.cruiseButtons();
  } catch {
    /* 快照线程未起时保持上次值 */
  }
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), 1000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

const enabled = computed(() => boolValue("CruiseButtonsEnabled"));
const stale = computed(() => snap.value?.stale ?? true);
const connected = computed(() => snap.value?.btConnected ?? false);

const btLabel = computed(() => {
  if (stale.value) return "无数据";
  if (!connected.value) return "未连接";
  const s = snap.value?.btState ?? 0;
  return s === 2 ? "故障" : s === 1 ? "执行中" : "就绪";
});
const btKind = computed(() => {
  if (stale.value) return "muted" as const;
  if (!connected.value) return "warn" as const;
  return (snap.value?.btState === 2 ? "danger" : "accent") as "danger" | "accent";
});

const btHint = computed(() => {
  if (connected.value) return "蓝牙模拟器已连接,守护进程在线。";
  if (stale.value) return "等设备上启用巡航按钮控制后,这里会显示模拟器与塑形状态。";
  return "未检测到蓝牙模拟器:请完成 BlueZ 配对(Just Works)并上电。守护进程会持续空转发布状态,不崩溃。";
});

const frozenKind = computed(() => (snap.value?.frozen ? "danger" : "accent") as "danger" | "accent");

const rows = computed(() => {
  if (stale.value || !snap.value) return [];
  const s = snap.value;
  const unexplained = s.unexplained ?? 0;
  const out: { label: string; value: string; kind?: "muted" | "warn" | "danger" | "info" | "accent" }[] = [
    { label: "用户上限", value: `${s.ceilingKph?.toFixed(0) ?? "—"} km/h` },
    { label: "公式目标", value: `${s.targetKph?.toFixed(0) ?? "—"} km/h` },
    { label: "当前 setSpeed", value: `${s.setSpeedKph?.toFixed(0) ?? "—"} km/h` },
    { label: "车距", value: s.leadPresent ? `有前车 ${s.leadSpeedKph?.toFixed(0) ?? "—"} km/h` : "无前车" },
  ];
  if (s.sccActive) out.push({ label: "弯道 (SCC-V)", value: "激活", kind: "accent" });
  if (s.standstill) out.push({ label: "停车", value: "standstill", kind: "info" });
  if (unexplained > 0) out.push({ label: "未解释回显", value: String(unexplained), kind: unexplained >= 3 ? "danger" : "warn" });
  return out;
});

function lastButtonLabel(v?: number): string {
  if (v === 0) return "RES+";
  if (v === 1) return "RES−";
  return "无";
}
</script>

<template>
  <div class="mb-3 flex flex-col gap-3 rounded-md bg-sl-surface-2 px-3 py-3 text-[12px]">
    <div>
      <div class="flex flex-wrap items-center gap-2">
        <span class="font-semibold text-sl-text-2">蓝牙模拟器</span>
        <Badge :kind="btKind">{{ btLabel }}</Badge>
        <Badge v-if="snap?.frozen" kind="danger">已冻结</Badge>
        <Badge v-else-if="enabled && !stale" :kind="frozenKind">运行中</Badge>
      </div>
      <p class="mt-1 leading-relaxed text-sl-text-3">{{ btHint }}</p>
    </div>

    <div v-if="rows.length" class="border-t border-sl-border/70 pt-3">
      <div class="grid grid-cols-2 gap-x-4 gap-y-1.5">
        <div v-for="r in rows" :key="r.label" class="flex items-center justify-between gap-2">
          <span class="text-sl-text-3">{{ r.label }}</span>
          <Badge v-if="r.kind" :kind="r.kind">{{ r.value }}</Badge>
          <span v-else class="font-semibold text-sl-text-2">{{ r.value }}</span>
        </div>
      </div>
      <p v-if="!stale" class="mt-2 text-sl-text-3">
        最近命令:{{ lastButtonLabel(snap?.lastButton) }} ·
        归属:{{ snap?.lastEchoOurs ? "我们" : "用户" }} ·
        按压量子:{{ snap?.quantumKph?.toFixed(2) ?? "—" }} km/h
      </p>
    </div>
  </div>
</template>
