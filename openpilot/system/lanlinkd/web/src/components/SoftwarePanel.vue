<script setup lang="ts">
/** 软件页：版本展示 / 检查更新 / 安装（对应 mici SoftwareLayoutMici）。
 *
 * 状态真真是 params 里的 updater 网络态：updater 进程靠 SIGUSR1 信号驱动
 * 检查，UpdateAvailable=True 时置 DoReboot 生效装新 release。2s 轮询
 * /api/software 参数快照，updaterState 会从 downloading/finalizing 走过，
 * 所以按"非 idle 即进行中"判断按钮可用性。
 */
import { onMounted, onUnmounted, ref } from "vue";
import { Loader2, RefreshCw, Rocket } from "lucide-vue-next";
import { api } from "@/lib/api";
import type { SoftwareStatus } from "@/lib/schema";
import { toast } from "@/lib/store";
import Badge from "./ui/Badge.vue";
import Button from "./ui/Button.vue";

const status = ref<SoftwareStatus | null>(null);
const loading = ref(true);
const pollError = ref("");
const busy = ref("");

let timer: ReturnType<typeof setInterval> | undefined;

async function poll(): Promise<void> {
  try {
    status.value = await api.software();
    pollError.value = "";
  } catch (e) {
    pollError.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function action(kind: "check" | "install"): Promise<void> {
  if (busy.value) return;
  busy.value = kind;
  try {
    await api.softwareOp(kind);
    if (kind === "check") toast("已请求检查更新，正在联网获取", "ok");
    else toast("安装已受理，设备将自动重启应用更新", "ok");
    await poll();
  } catch (e) {
    toast(e instanceof Error ? e.message : "操作失败", "error");
  } finally {
    busy.value = "";
  }
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), 2000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="flex flex-col gap-4">
    <div v-if="loading" class="grid h-40 place-items-center">
      <Loader2 class="size-6 animate-spin text-sl-text-3" />
    </div>

    <template v-else-if="status">
      <!-- 故障提示 -->
      <p
        v-if="pollError"
        class="rounded-lg bg-sl-danger/10 px-4 py-2.5 text-[13px] text-sl-danger ring-1 ring-inset ring-sl-danger/25"
      >
        软件服务不可用：{{ pollError }}
      </p>

      <!-- 版本 -->
      <section class="sl-card px-5 py-4">
        <dl class="grid grid-cols-[56px_1fr] gap-x-3 gap-y-1 text-[13px]">
          <dt class="text-sl-text-3">版本</dt><dd>{{ status.version || "-" }}</dd>
          <dt class="text-sl-text-3">分支</dt><dd>{{ status.branch || "-" }}<span v-if="status.commit"> · {{ status.commit }}</span></dd>
          <dt class="text-sl-text-3">渠道</dt><dd>{{ status.targetBranch || "默认" }}</dd>
        </dl>
        <p v-if="status.failed" class="mt-2 text-[13px] text-sl-danger">
          上次更新失败（重试 {{ status.failedCount }} 次）
        </p>
        <Badge v-else-if="status.updaterState !== 'idle'" kind="info">
          {{ status.updaterState }}
        </Badge>
      </section>

      <!-- 检查更新 -->
      <section class="sl-card px-5 py-4">
        <div class="flex items-center gap-3">
          <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-sl-surface-2">
            <RefreshCw class="size-5 text-sl-text-2" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">检查更新</h2>
            <p class="mt-0.5 text-[13px] text-sl-text-2">
              {{ status.fetchAvailable ? "有新版本可下载" : "已是最新版本" }}
            </p>
          </div>
          <Button variant="accent" :disabled="!status.offroad || !!busy" class="min-w-20 justify-center"
                  @click="action('check')">
            <Loader2 v-if="busy === 'check' || status.updaterState !== 'idle'" class="mr-2 size-4 animate-spin" />
            CHECK
          </Button>
        </div>
        <p v-if="!status.offroad" class="mt-3 rounded-lg bg-sl-surface-2 px-3 py-2 text-[13px] text-sl-text-3">
          检查与安装均限停车（offroad）操作。
        </p>
      </section>

      <!-- 安装 -->
      <section v-if="status.updateAvailable" class="sl-card px-5 py-4">
        <div class="flex items-center gap-3">
          <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-sl-surface-2">
            <Rocket class="size-5 text-sl-text-2" />
          </div>
          <div class="min-w-0 flex-1">
            <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">安装更新</h2>
            <p class="mt-0.5 text-[13px] text-sl-text-2">
              {{ status.newVersion ? `${status.newVersion.version}（${status.newVersion.branch}）` : "新版本" }}
            </p>
          </div>
          <Button variant="accent" :disabled="!status.offroad || !!busy || status.updaterState !== 'idle'"
                  class="min-w-20 justify-center"
                  @click="action('install')">
            <Loader2 v-if="busy === 'install'" class="mr-2 size-4 animate-spin" />INSTALL
          </Button>
        </div>
        <p class="mt-3 rounded-lg bg-sl-surface-2 px-3 py-2 text-[13px] text-sl-text-3">
          安装将通过设备自动重启完成，请保证供电稳定（勿断电）。
        </p>
      </section>
    </template>
  </div>
</template>
