<script setup lang="ts">
/** 登录 / 首次设置密码。
 *
 * 首次使用判定走 /api/setup（返回 400 = 未设密码，409 = 已设），
 * **不能**用 /api/login 试空密码——那会累计 LoginThrottle 计数，
 * 刷新五次登录页就把自己锁 300 秒（见 lib/api.ts）。
 */
import { onMounted, ref } from "vue";
import { Activity, Loader2 } from "lucide-vue-next";
import Button from "./ui/Button.vue";
import { ApiError, api, needsSetup, setToken } from "@/lib/api";

const emit = defineEmits<{ authed: [] }>();

const mode = ref<"probing" | "login" | "setup">("probing");
const password = ref("");
const confirm = ref("");
const busy = ref(false);
const error = ref("");

onMounted(async () => {
  mode.value = (await needsSetup()) ? "setup" : "login";
});

async function submit(): Promise<void> {
  error.value = "";
  if (password.value.length < 6) {
    error.value = "密码至少 6 位";
    return;
  }
  if (mode.value === "setup" && password.value !== confirm.value) {
    error.value = "两次输入的密码不一致";
    return;
  }

  busy.value = true;
  try {
    if (mode.value === "setup") await api.setup(password.value);
    const { token } = await api.login(password.value);
    setToken(token);
    emit("authed");
  } catch (e) {
    error.value = describe(e);
  } finally {
    busy.value = false;
  }
}

function describe(e: unknown): string {
  if (!(e instanceof ApiError)) return "网络错误，请检查与设备的连接";
  switch (e.status) {
    case 401:
      return "密码错误";
    // 防爆破锁定：把设备返回的剩余秒数原样透出，用户才知道要等多久
    case 429:
      return e.message.replace(/locked, retry in (\d+)s/, "尝试次数过多，请 $1 秒后再试");
    case 409:
      return "此设备已设置过密码，请直接登录";
    case 400:
      return "密码至少 6 位";
    default:
      return e.message;
  }
}
</script>

<template>
  <div class="grid min-h-dvh place-items-center bg-sl-bg px-4">
    <div class="w-full max-w-[380px]">
      <div class="mb-8 flex flex-col items-center gap-3">
        <div class="grid size-12 place-items-center rounded-xl bg-sl-accent/15 ring-1 ring-inset ring-sl-accent/30">
          <Activity class="size-6 text-sl-accent" />
        </div>
        <div class="text-center">
          <h1 class="text-xl font-semibold tracking-tight text-sl-text-1">LANLink</h1>
          <p class="mt-1 text-[13px] text-sl-text-3">
            {{ mode === "setup" ? "首次使用，请设置访问密码" : "请输入访问密码" }}
          </p>
        </div>
      </div>

      <div v-if="mode === 'probing'" class="flex justify-center py-6">
        <Loader2 class="size-5 animate-spin text-sl-text-3" />
      </div>

      <form v-else class="sl-card flex flex-col gap-3 p-5" @submit.prevent="submit">
        <label class="flex flex-col gap-1.5">
          <span class="text-[12px] font-medium text-sl-text-2">密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            class="h-11 rounded-lg bg-sl-bg px-3 text-[15px] text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border transition-shadow focus:ring-2 focus:ring-sl-accent"
          />
        </label>

        <label v-if="mode === 'setup'" class="flex flex-col gap-1.5">
          <span class="text-[12px] font-medium text-sl-text-2">确认密码</span>
          <input
            v-model="confirm"
            type="password"
            autocomplete="new-password"
            class="h-11 rounded-lg bg-sl-bg px-3 text-[15px] text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border transition-shadow focus:ring-2 focus:ring-sl-accent"
          />
        </label>

        <p v-if="error" class="text-[13px] text-sl-danger">{{ error }}</p>

        <Button type="submit" variant="accent" size="lg" :disabled="busy" class="mt-1 w-full">
          <Loader2 v-if="busy" class="size-4 animate-spin" />
          {{ mode === "setup" ? "设置并登录" : "登录" }}
        </Button>
      </form>

      <p class="mt-4 text-center text-[11px] leading-relaxed text-sl-text-3">
        仅限局域网访问。密码以哈希形式存储在设备本地。
      </p>
    </div>
  </div>
</template>
