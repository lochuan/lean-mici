<script setup lang="ts">
/** WiFi 页：网络列表 / 连接（可选静态 IP）/ 已保存网络管理。
 *
 * 状态真身在 lanlinkd 进程内的 WifiManager(manage_tethering=False)，
 * 2s 页面级轮询（同 BluetoothPanel 节奏）。connect/static/forget 在行车中
 * 被后端 409 拦下——与蓝牙页的 offroad 文案一致。
 * 静态 IP 语义：按 SSID 存 NM profile，method=manual；DNS 必填（manual
 * 没有 DHCP 兜底，空 DNS 会静默断网）。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { Loader2, Wifi } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { WifiIpv4, WifiStatus } from "@/lib/schema";
import { toast } from "@/lib/store";
import Button from "./ui/Button.vue";

const status = ref<WifiStatus | null>(null);
const loading = ref(true);
const pollError = ref("");
const busy = ref("");

/** 连接弹层：目标网络 + 密码 + 可展开的静态 IP 字段 */
const connectTarget = ref<{ ssid: string; saved: boolean } | null>(null);
const connectPassword = ref("");
const staticOn = ref(false);
const staticIp = ref("");
const staticPrefix = ref("24");
const staticGateway = ref("");
const staticDns = ref("");

/** 静态 IP 编辑弹层（对已保存网络） */
const editTarget = ref<string | null>(null);
const editIp = ref("");
const editPrefix = ref("24");
const editGateway = ref("");
const editDns = ref("");

let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    status.value = await api.wifi();
    pollError.value = "";
  } catch (e) {
    // 保留上一帧；首次进入失败也仍让面板可用（显示降级态）
    pollError.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

function openConnect(ssid: string, saved: boolean): void {
  connectTarget.value = { ssid, saved };
  connectPassword.value = "";
  staticOn.value = false;
  staticIp.value = "";
  staticPrefix.value = "24";
  staticGateway.value = "";
  staticDns.value = "";
}

function parseFields(ip: string, prefix: string, gateway: string, dns: string):
  { ip: string; prefix: number; gateway: string; dns: string[] } | null {
  const dnsList = dns.split(",").map((d) => d.trim()).filter(Boolean);
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) {
    toast("IP 地址格式不正确", "error");
    return null;
  }
  const p = Number(prefix) || 24;
  if (p < 1 || p > 32) {
    toast("前缀长度需在 1-32", "error");
    return null;
  }
  if (!/^(\d{1,3})(\.\d{1,3}){3}$/.test(gateway)) {
    toast("网关格式不正确", "error");
    return null;
  }
  if (dnsList.length === 0) {
    toast("静态模式至少需要一个 DNS 服务器", "error");
    return null;
  }
  for (const d of dnsList) {
    if (!/^(\d{1,3})(\.\d{1,3}){3}$/.test(d)) {
      toast(`DNS 格式不正确: ${d}`, "error");
      return null;
    }
  }
  return { ip, prefix: p, gateway, dns: dnsList };
}

async function doConnect(): Promise<void> {
  const t = connectTarget.value;
  if (!t || busy.value) return;
  const body: Record<string, unknown> = { ssid: t.ssid, password: connectPassword.value };
  if (staticOn.value) {
    const cfg = parseFields(staticIp.value, staticPrefix.value, staticGateway.value, staticDns.value);
    if (cfg === null) return;
    body.static = cfg;
  }
  busy.value = "connect";
  try {
    await api.wifiOp("connect", body);
    connectTarget.value = null;
    await poll();
  } catch (e) {
    toast(e instanceof Error ? e.message : "连接失败", "error");
  } finally {
    busy.value = "";
  }
}

async function doStatic(): Promise<void> {
  const ssid = status.value?.connected ?? status.value?.connecting;
  if (!ssid || busy.value) return;
  const cfg = editTarget.value === null ? null : parseFields(editIp.value, editPrefix.value, editGateway.value, editDns.value);
  if (cfg === null) return;
  busy.value = "static";
  try {
    await api.wifiOp("static", { ssid, ...cfg });
    editTarget.value = null;
    await poll();
  } catch (e) {
    toast(e instanceof Error ? e.message : "保存失败", "error");
  } finally {
    busy.value = "";
  }
}

async function doForget(ssid: string): Promise<void> {
  if (busy.value) return;
  busy.value = "forget";
  try {
    await api.wifiOp("forget", { ssid });
    await poll();
  } catch (e) {
    toast(e instanceof Error ? e.message : "删除失败", "error");
  } finally {
    busy.value = "";
  }
}

function openStaticEditor(ipv4: WifiIpv4 | undefined, ssid: string): void {
  if (!ipv4) return;
  staticOn.value = true;
  editIp.value = ipv4.addresses[0]?.split("/")[0] ?? "";
  editPrefix.value = ipv4.addresses[0]?.split("/")[1] ?? "24";
  editGateway.value = ipv4.gateway ?? "";
  editDns.value = ipv4.dns.join(", ");
  editTarget.value = ssid;
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), 2000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});

const networks = computed(() => status.value?.networks ?? []);

const writesBlocked = computed(() => !!busy.value || !status.value?.offroad);
</script>

<template>
  <div class="flex flex-col gap-4">
    <div v-if="loading" class="grid h-40 place-items-center">
      <Loader2 class="size-6 animate-spin text-sl-text-3" />
    </div>

    <template v-else-if="status">
      <!-- 当前连接 -->
      <section v-if="status.connected || status.connecting" class="sl-card px-5 py-4">
        <div class="flex items-center gap-3">
          <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-sl-surface-2">
            <Wifi v-if="status.connected" class="size-5 text-sl-text-2" />
            <Loader2 v-else class="size-5 animate-spin text-sl-text-3" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">当前连接</h2>
            <p class="mt-0.5 truncate text-[13px] text-sl-text-2">
              {{ status.connecting && !status.connected ? `${status.connecting}（连接中…）` : status.connected }}
            </p>
          </div>
        </div>
        <dl v-if="status.connected" class="mt-3 grid grid-cols-[56px_1fr] gap-x-3 gap-y-1 text-[13px]">
          <dt class="text-sl-text-3">IP</dt>
          <dd>{{ status.ipv4.addresses.join(", ") || "-" }}</dd>
          <dt class="text-sl-text-3">网关</dt>
          <dd>{{ status.ipv4.gateway || "-" }}</dd>
          <dt class="text-sl-text-3">DNS</dt>
          <dd>{{ status.ipv4.dns.join(", ") || "-" }}</dd>
          <dt class="text-sl-text-3">方式</dt>
          <dd>{{ status.ipv4.method === "manual" ? "静态" : "DHCP" }}</dd>
        </dl>
      </section>

      <!-- 当前连接的网络刻意不在列表里重复出现 forget 入口，误触会断网 -->
      <section
        v-if="status.connected && status.ipv4.addresses.length > 0"
        class="sl-card px-5 py-4"
      >
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">静态 IP（{{ status.connected }}）</h2>
        <p class="mt-1 text-[13px] text-sl-text-2">
          当前方式：{{ status.ipv4.method === "manual" ? "静态" : "DHCP 自动获取" }}
        </p>
        <Button class="mt-3 w-full justify-center" :disabled="writesBlocked" @click="openStaticEditor(status.ipv4, status.connected)">
          编辑 IP / 网关 / DNS
        </Button>
      </section>

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
        网络服务不可用：{{ pollError }}
      </p>

      <!-- 网络列表 -->
      <section class="sl-card px-5 py-4">
        <h2 class="mb-2 text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">网络</h2>
        <p v-if="!networks.length" class="text-[13px] text-sl-text-2">没有扫描到网络</p>
        <div class="divide-y divide-sl-border/70">
          <div v-for="n in networks" :key="n.ssid" class="flex items-center justify-between py-2.5">
            <div class="min-w-0">
              <p class="truncate text-[15px] font-medium">{{ n.ssid }}</p>
              <p class="text-[12px] text-sl-text-2">
                <span v-if="n.saved">已保存 · </span>
                <span class="truncate">{{ n.security || "open" }}</span>
              </p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <Button
                v-if="n.saved && n.ssid !== status.connected"
                variant="ghost"
                :disabled="writesBlocked"
                class="px-2"
                @click="doForget(n.ssid)"
              >
                忘记
              </Button>
              <Button :disabled="writesBlocked" class="min-w-20 justify-center" @click="openConnect(n.ssid, n.saved)">
                连接
              </Button>
            </div>
          </div>
        </div>
      </section>

      <!-- 连接弹层：密码 + 可选静态 IP -->
      <div v-if="connectTarget" class="sl-card px-5 py-4" role="dialog" aria-modal="true">
        <h2 class="text-[15px] font-semibold">连接到 {{ connectTarget.ssid }}</h2>
        <input
          v-model="connectPassword"
          type="password"
          placeholder="密码（开放网络可留空）"
          class="mt-3 w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]"
        >
        <button class="mt-2 text-[13px] text-sl-accent underline underline-offset-4" @click="staticOn = !staticOn">
          {{ staticOn ? "使用 DHCP 自动获取" : "配置静态 IP…" }}
        </button>
        <div v-if="staticOn" class="mt-2 space-y-2">
          <input v-model="staticIp" type="text" placeholder="IP e.g. 192.168.1.50"
                 class="w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
          <input v-model="staticPrefix" type="number" min="1" max="32" placeholder="前缀 e.g. 24"
                 class="w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
          <input v-model="staticGateway" type="text" placeholder="网关 e.g. 192.168.1.1"
                 class="w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
          <input v-model="staticDns" type="text" placeholder="DNS，逗号分隔 e.g. 1.1.1.1,8.8.8.8"
                 class="w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
        </div>
        <div class="mt-4 flex gap-2">
          <Button class="flex-1 justify-center" :disabled="!!busy" @click="doConnect">
            <Loader2 v-if="busy === 'connect'" class="mr-2 size-4 animate-spin" />连接
          </Button>
          <Button class="flex-1 justify-center" variant="ghost" @click="connectTarget = null">取消</Button>
        </div>
      </div>

      <!-- 静态 IP 编辑弹层 -->
      <div v-if="editTarget" class="sl-card px-5 py-4" role="dialog" aria-modal="true">
        <h2 class="text-[15px] font-semibold">静态 IP · {{ editTarget }}</h2>
        <input v-model="editIp" type="text" placeholder="IP e.g. 192.168.1.50"
               class="mt-3 w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
        <input v-model="editPrefix" type="number" min="1" max="32" placeholder="前缀 e.g. 24"
               class="mt-2 w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
        <input v-model="editGateway" type="text" placeholder="网关 e.g. 192.168.1.1"
               class="mt-2 w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
        <input v-model="editDns" type="text" placeholder="DNS，逗号分隔 e.g. 1.1.1.1,8.8.8.8"
               class="mt-2 w-full rounded-lg border border-sl-border bg-transparent px-3 py-2 text-[15px]">
        <p class="mt-2 text-[12px] text-sl-text-2">DNS 必填；保存后立即对该 SSID 生效并持久化</p>
        <div class="mt-4 flex gap-2">
          <Button class="flex-1 justify-center" :disabled="!!busy" @click="doStatic">
            <Loader2 v-if="busy === 'static'" class="mr-2 size-4 animate-spin" />保存并生效
          </Button>
          <Button class="flex-1 justify-center" variant="surface" @click="editTarget = null">取消</Button>
        </div>
      </div>
    </template>
  </div>
</template>
