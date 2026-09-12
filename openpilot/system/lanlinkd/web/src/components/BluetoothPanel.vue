<script setup lang="ts">
/** 蓝牙页：电源 / 搜索 / 设备列表 + 配对提示 + 音频测试倒计时。
 *
 * 状态真身在 bluetooth_managerd，这里 1s 页面级轮询（同 StatusPanel，
 * 不进全局 pollStatus——那个还承担 paramsVersion 同步）。纯逻辑在
 * lib/bluetooth.ts；按钮可用性与 the_galaxy 的 BluetoothPanel.js 对齐。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Bluetooth, BluetoothOff, Loader2 } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { BluetoothDevice, BluetoothStatus } from "@/lib/schema";
import {
  audioTestLabel, audioTestPhase, capabilityText, deviceStatusText, groupDevices,
  isAudioSelected, isPairing, needsPairValue, promptDescription,
} from "@/lib/bluetooth";
import { toast } from "@/lib/store";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";
import Switch from "./ui/Switch.vue";

const status = ref<BluetoothStatus | null>(null);
const loading = ref(true);
/** 传输层错误（503 等）；成功一帧即清空，与 status.error（daemon 报的）分开展示 */
const pollError = ref("");
/** 操作锁：同一时刻只放一个操作在飞，按钮按 operation 名细分禁用 */
const busy = ref("");
const pairValue = ref("");
/** 音频测试：deadline 是 epoch ms，0 = 请求还在路上（starting 相） */
const testDeadline = ref(0);
const nowTick = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    status.value = await api.bluetooth();
    pollError.value = "";
  } catch (e) {
    // 401 走全局 handler；其余失败保留上一帧，配对流程不因一次超时被打断
    pollError.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function request(operation: string, body: Record<string, unknown> = {}): Promise<void> {
  if (busy.value) return;
  busy.value = operation;
  try {
    const res = await api.bluetoothOp(operation, body);
    if (typeof res.audio_test_delay_ms === "number") {
      testDeadline.value = Date.now() + res.audio_test_delay_ms;
    }
    await poll();
  } catch (e) {
    toast(e instanceof Error ? e.message : "蓝牙操作失败", "error");
  } finally {
    busy.value = "";
  }
}

onMounted(() => {
  void poll();
  // 轮询和倒计时共用一个 1s tick
  timer = setInterval(() => {
    nowTick.value = Date.now();
    void poll();
  }, 1000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

const groups = computed(() => groupDevices(status.value?.devices ?? []));
const prompt = computed(() => status.value?.prompt ?? null);

const testPhase = computed(() => audioTestPhase(testDeadline.value, nowTick.value));
/** 请求在飞（starting）或倒计时未走完时展示卡片；complete 即收起 */
const testVisible = computed(
  () => busy.value === "test_audio" || (testDeadline.value > 0 && testPhase.value.phase !== "complete"),
);
watch(testPhase, (p) => {
  if (p.phase === "complete") testDeadline.value = 0;
});

function togglePower(): void {
  const s = status.value;
  if (!s) return;
  void request("power", { enabled: !s.enabled });
}

function toggleScan(): void {
  const s = status.value;
  if (!s) return;
  void request(s.discovering ? "stop_scan" : "scan");
}

function connectDevice(d: BluetoothDevice): void {
  void request(d.connected ? "disconnect" : "connect", { address: d.address });
}

function toggleAudio(d: BluetoothDevice): void {
  // 已选中 → 空地址 = 取消选择
  const selected = !!status.value && isAudioSelected(d, status.value);
  void request("select_audio", { address: selected ? "" : d.address });
}

function testAudio(d: BluetoothDevice): void {
  testDeadline.value = 0;
  void request("test_audio", { address: d.address });
}

function respondPairing(accepted: boolean): void {
  const p = prompt.value;
  if (!p || busy.value) return;
  if (accepted && needsPairValue(p) && !pairValue.value.trim()) {
    toast("请输入配对数值后再继续", "warn");
    return;
  }
  void request("pairing_response", { prompt_id: p.id, accepted, value: pairValue.value.trim() });
  pairValue.value = "";
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <div v-if="loading" class="grid h-40 place-items-center">
      <Loader2 class="size-6 animate-spin text-sl-text-3" />
    </div>

    <template v-else-if="status">
      <!-- 电源 -->
      <section class="sl-card px-5 py-4">
        <div class="flex items-center gap-3">
          <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-sl-surface-2">
            <Bluetooth v-if="status.enabled" class="size-5 text-sl-text-2" />
            <BluetoothOff v-else class="size-5 text-sl-text-3" />
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">蓝牙</h2>
              <Badge v-if="!status.available" kind="muted">不可用</Badge>
              <Badge v-if="!status.offroad" kind="warn">行车中</Badge>
            </div>
            <p class="mt-0.5 text-[13px] text-sl-text-2">{{ status.enabled ? "已开启" : "已关闭" }}</p>
          </div>
          <Switch
            :model-value="status.enabled"
            :disabled="!status.available || !status.offroad || !!busy"
            :pending="busy === 'power'"
            aria-label="蓝牙电源"
            @update:model-value="togglePower"
          />
        </div>
        <p v-if="!status.offroad" class="mt-3 rounded-lg bg-sl-surface-2 px-3 py-2 text-[13px] text-sl-text-3">
          搜索、配对与遗忘设备仅限停车（offroad）时操作；行车中仍可断开设备或切换音频输出。
        </p>
      </section>

      <!-- 错误：daemon 报的（配对失败等）与传输层失败（503）分两条 -->
      <p
        v-if="status.error"
        class="rounded-lg bg-sl-danger/10 px-4 py-2.5 text-[13px] text-sl-danger ring-1 ring-inset ring-sl-danger/25"
      >
        {{ status.error }}
      </p>
      <p
        v-else-if="pollError"
        class="rounded-lg bg-sl-danger/10 px-4 py-2.5 text-[13px] text-sl-danger ring-1 ring-inset ring-sl-danger/25"
      >
        蓝牙服务不可用：{{ pollError }}
      </p>

      <!-- 配对请求（bluez agent） -->
      <section v-if="prompt" class="sl-card px-5 py-4">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
          配对请求 · {{ prompt.name || "未知设备" }}
        </h2>
        <p class="mt-1 text-[13px] text-sl-text-2">{{ promptDescription(prompt) }}</p>
        <p v-if="prompt.value" class="sl-tabular mt-2 font-mono text-lg tracking-widest text-sl-text-1">
          {{ prompt.value }}
        </p>
        <input
          v-if="needsPairValue(prompt)"
          v-model="pairValue"
          inputmode="numeric"
          placeholder="配对数值"
          class="mt-3 h-10 w-full max-w-[240px] rounded-lg bg-sl-bg px-3 text-sm text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border placeholder:text-sl-text-3 focus:ring-sl-accent"
        />
        <div v-if="!prompt.display_only" class="mt-3 flex gap-2">
          <Button variant="ghost" :disabled="busy === 'pairing_response'" @click="respondPairing(false)">
            取消
          </Button>
          <Button variant="accent" :disabled="busy === 'pairing_response'" @click="respondPairing(true)">
            允许
          </Button>
        </div>
      </section>

      <!-- 音频测试倒计时 -->
      <section v-if="testVisible" class="sl-card px-5 py-4">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">音频测试</h2>
        <div class="mt-2 flex items-center gap-3">
          <Loader2 v-if="testPhase.phase === 'countdown'" class="size-4 animate-spin text-sl-accent" />
          <span class="sl-tabular text-2xl font-semibold text-sl-text-1">
            {{ audioTestLabel(testPhase.phase, testPhase.seconds) }}
          </span>
          <span class="text-[13px] text-sl-text-3">测试音即将在选定的音频设备上播放</span>
        </div>
      </section>

      <!-- 搜索 -->
      <div class="flex items-center gap-2">
        <Button
          variant="accent"
          :disabled="!status.offroad || !status.enabled || !!busy"
          @click="toggleScan"
        >
          <Loader2 v-if="status.discovering" class="size-3.5 animate-spin" />
          {{ status.discovering ? "搜索中…" : "搜索设备" }}
        </Button>
      </div>

      <!-- 我的设备 -->
      <section class="sl-card px-5 py-4">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">我的设备</h2>
        <p v-if="!groups.known.length" class="mt-2 text-[13px] text-sl-text-3">尚无已保存的设备。</p>
        <div v-else class="mt-1 divide-y divide-sl-border/70">
          <div v-for="d in groups.known" :key="d.address" class="flex flex-wrap items-center gap-3 py-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="truncate text-sm text-sl-text-1">{{ d.name }}</span>
                <Badge v-if="d.connected" kind="accent">已连接</Badge>
                <Badge v-if="isAudioSelected(d, status)" kind="info">音频输出</Badge>
                <Badge v-if="isPairing(d, status)" kind="warn">配对中</Badge>
              </div>
              <p class="mt-0.5 truncate text-[13px] text-sl-text-3">
                {{ capabilityText(d) }} · {{ deviceStatusText(d, status) }}
              </p>
            </div>
            <div class="flex shrink-0 flex-wrap items-center gap-2">
              <Button
                v-if="d.paired || d.connected"
                size="sm"
                :disabled="!!busy"
                @click="connectDevice(d)"
              >
                {{ d.connected ? "断开" : "连接" }}
              </Button>
              <Button v-if="d.audio" size="sm" :disabled="!!busy" @click="toggleAudio(d)">
                {{ isAudioSelected(d, status) ? "停止用于音频" : "用于音频" }}
              </Button>
              <Button
                v-if="d.audio && d.connected"
                size="sm"
                :disabled="!status.offroad || !!busy"
                @click="testAudio(d)"
              >
                测试音频
              </Button>
              <Button
                v-if="d.paired"
                size="sm"
                variant="danger"
                :disabled="!status.offroad || !!busy"
                @click="request('forget', { address: d.address })"
              >
                遗忘
              </Button>
            </div>
          </div>
        </div>
      </section>

      <!-- 可用设备 -->
      <section class="sl-card px-5 py-4">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">可用设备</h2>
        <p v-if="!groups.available.length" class="mt-2 text-[13px] text-sl-text-3">
          {{ status.discovering ? "正在搜索附近设备…" : "未发现附近设备。" }}
        </p>
        <div v-else class="mt-1 divide-y divide-sl-border/70">
          <div v-for="d in groups.available" :key="d.address" class="flex flex-wrap items-center gap-3 py-3">
            <div class="min-w-0 flex-1">
              <span class="truncate text-sm text-sl-text-1">{{ d.name }}</span>
              <p class="mt-0.5 truncate text-[13px] text-sl-text-3">
                {{ capabilityText(d) }} · {{ deviceStatusText(d, status) }}
              </p>
            </div>
            <Button
              size="sm"
              variant="accent"
              :disabled="!status.offroad || !!busy || isPairing(d, status)"
              @click="request('pair', { address: d.address })"
            >
              {{ isPairing(d, status) ? "配对中…" : "配对" }}
            </Button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
