<script setup lang="ts">
/** 车辆页。对齐 sunnylink /dashboard/settings/vehicle（实地勘察 2026-09-12）：
 *   一张卡片，显示车型名 + 指纹状态，右侧一个 Select 按钮。
 *
 * 与 sunnylink 的差别：它连不上设备时只能显示 "No vehicle detected"，
 * 我们是直连设备，所以能把指纹推导出的能力（VIN/转向方式/纵向控制）一并列出——
 * 这些在排查"为什么某个开关是灰的"时很有用。
 */
import { computed, onMounted, ref } from "vue";
import { Car, Check, Loader2, Search, X } from "lucide-vue-next";
import Button from "./ui/Button.vue";
import Badge from "./ui/Badge.vue";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import { cn } from "@/lib/utils";
import type { VehicleState } from "@/lib/schema";

const state = ref<VehicleState | null>(null);
const loading = ref(true);
const picking = ref(false);
const saving = ref(false);
const query = ref("");

async function load(): Promise<void> {
  loading.value = true;
  try {
    state.value = await api.vehicle();
  } catch (e) {
    toast(e instanceof Error ? e.message : "读取车辆信息失败", "error");
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const title = computed(() => {
  const s = state.value;
  if (!s) return "";
  if (s.name) return s.name;
  if (s.platform) return s.platform; // 自动识别但 car_list 里没有对应显示名
  return "未识别到车辆";
});

/** 指纹来源徽章：手动指定 / 自动识别 / 未识别 */
const badge = computed(() => {
  switch (state.value?.source) {
    case "manual":
      return { text: "手动指定", kind: "info" as const };
    case "auto":
      return { text: "自动识别", kind: "accent" as const };
    default:
      return { text: "未识别", kind: "warn" as const };
  }
});

const results = computed(() => {
  const all = state.value?.choices ?? [];
  const q = query.value.trim().toLowerCase();
  if (!q) return all;
  // 逐词匹配：「sienna 2021」这种输入要能命中
  const words = q.split(/\s+/);
  return all.filter((n) => {
    const hay = n.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
});

/** 指纹推导出的只读信息。手动指定车型后这里仍是上次点火的识别结果。 */
const facts = computed(() => {
  const d = state.value?.detected ?? {};
  const rows: { label: string; value: string }[] = [];
  if (d.vin) rows.push({ label: "VIN", value: d.vin });
  if (d.platform) rows.push({ label: "识别平台", value: d.platform });
  if (d.steer_control_type) rows.push({ label: "转向控制", value: d.steer_control_type });
  if (d.openpilot_longitudinal !== undefined) {
    rows.push({ label: "纵向控制", value: d.openpilot_longitudinal ? "openpilot" : "原车 ACC" });
  }
  if (d.enable_bsm !== undefined) rows.push({ label: "盲区监测", value: d.enable_bsm ? "有" : "无" });
  if (d.mass_kg) rows.push({ label: "整车质量", value: `${d.mass_kg} kg` });
  if (d.wheelbase_m) rows.push({ label: "轴距", value: `${d.wheelbase_m} m` });
  return rows;
});

async function choose(name: string): Promise<void> {
  saving.value = true;
  try {
    await api.selectVehicle(name);
    await load();
    picking.value = false;
    query.value = "";
    // 车型只在下次点火时生效，不说清楚用户会以为没保存上
    toast(name ? "已指定车型，下次上电生效" : "已恢复自动识别", "ok");
  } catch (e) {
    toast(e instanceof Error ? e.message : "保存失败", "error");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="mb-8">
    <h2 class="mb-1 text-[13px] font-medium uppercase tracking-wide text-sl-text-3">车辆</h2>
    <p class="mb-3 text-[13px] text-sl-text-3">指纹识别与平台选择</p>

    <div class="rounded-xl border border-sl-border bg-sl-surface">
      <div v-if="loading" class="grid h-24 place-items-center">
        <Loader2 class="size-5 animate-spin text-sl-text-3" />
      </div>

      <template v-else-if="state">
        <div class="flex items-center gap-3 p-4">
          <div class="grid size-10 shrink-0 place-items-center rounded-lg bg-sl-surface-2">
            <Car class="size-5 text-sl-text-2" />
          </div>
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm text-sl-text-1">{{ title }}</p>
            <div class="mt-1 flex items-center gap-2">
              <Badge :kind="badge.kind">{{ badge.text }}</Badge>
              <span v-if="state.brand" class="text-xs text-sl-text-3">{{ state.brand }}</span>
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <Button
              v-if="state.source === 'manual'"
              size="sm"
              variant="ghost"
              :disabled="saving"
              @click="choose('')"
            >
              恢复自动
            </Button>
            <Button size="sm" :disabled="saving" @click="picking = !picking">
              {{ picking ? "取消" : "选择车型" }}
            </Button>
          </div>
        </div>

        <!-- 车型选择：72 条列表，必须能搜 -->
        <div v-if="picking" class="border-t border-sl-border p-4">
          <div class="relative mb-3">
            <Search class="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-sl-text-3" />
            <input
              v-model="query"
              type="search"
              placeholder="搜索车型，如 sienna 2021"
              class="h-9 w-full rounded-lg bg-sl-bg pl-9 pr-3 text-[13px] text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border placeholder:text-sl-text-3 focus:ring-sl-accent"
            />
            <button
              v-if="query"
              type="button"
              class="absolute right-2 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-sl-text-3 hover:text-sl-text-1"
              @click="query = ''"
            >
              <X class="size-3.5" />
            </button>
          </div>

          <p v-if="!results.length" class="py-6 text-center text-[13px] text-sl-text-3">
            没有匹配的车型
          </p>

          <ul v-else class="max-h-[min(420px,50vh)] overflow-y-auto">
            <li v-for="name in results" :key="name">
              <button
                type="button"
                :disabled="saving"
                :class="cn(
                  'flex h-11 w-full items-center gap-2 rounded-lg px-3 text-left text-[13px] transition-colors',
                  name === state.name ? 'text-sl-accent' : 'text-sl-text-2',
                  'hover:bg-sl-surface-2 hover:text-sl-text-1 disabled:opacity-50',
                )"
                @click="choose(name)"
              >
                <Check v-if="name === state.name" class="size-3.5 shrink-0" />
                <span class="truncate">{{ name }}</span>
              </button>
            </li>
          </ul>
        </div>

        <!-- 指纹推导出的只读能力 -->
        <dl v-if="facts.length" class="grid grid-cols-2 gap-x-6 border-t border-sl-border p-4">
          <div v-for="f in facts" :key="f.label" class="flex h-8 items-center justify-between gap-3">
            <dt class="text-[13px] text-sl-text-3">{{ f.label }}</dt>
            <dd class="truncate font-mono text-xs text-sl-text-2">{{ f.value }}</dd>
          </div>
        </dl>
      </template>
    </div>
  </section>
</template>
