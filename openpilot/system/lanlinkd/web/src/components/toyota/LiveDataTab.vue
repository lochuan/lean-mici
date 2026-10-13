<script setup lang="ts">
/** 实时数据页：配置 + 按 ECU 分组勾选 PID（≤40）+ 1 s 轮询表格。
 *  轮询只在「本页可见 + 浏览器页面可见 + 已勾选」时进行，且上一请求未返回不发下一次。 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Loader2 } from "lucide-vue-next";
import { api } from "@/lib/api";
import { toast } from "@/lib/store";
import type { ToyotaPidCatalog, ToyotaPidValue } from "@/lib/schema";
import { PID_SELECTION_LIMIT, errorText, filterPidGroups, togglePid } from "@/lib/toyota";
import Accordion from "../ui/Accordion.vue";
import Checkbox from "../ui/Checkbox.vue";
import Input from "../ui/Input.vue";
import Select from "../ui/Select.vue";

const props = defineProps<{ active: boolean }>();

const catalog = ref<ToyotaPidCatalog | null>(null);
const loading = ref(false);
const switching = ref(false);
const query = ref("");
const selected = ref<number[]>([]);
const values = ref<Record<number, ToyotaPidValue>>({});
const pageVisible = ref(document.visibilityState === "visible");

const visibleGroups = computed(() => filterPidGroups(catalog.value?.groups ?? [], query.value));
const entries = computed(() =>
  visibleGroups.value.map((g) => ({ id: g.ecu, label: g.ecu, count: g.pids.length })),
);
const pidsOf = (ecu: string) => visibleGroups.value.find((g) => g.ecu === ecu)?.pids ?? [];
const selectedPids = computed(() => {
  const all = (catalog.value?.groups ?? []).flatMap((g) => g.pids);
  return selected.value.flatMap((id) => all.find((p) => p.id === id) ?? []);
});
const profileOptions = computed(() =>
  (catalog.value?.profiles ?? []).map((p) => ({ value: p.id, label: p.name })),
);

async function loadCatalog(): Promise<void> {
  if (loading.value) return;
  loading.value = true;
  try {
    catalog.value = await api.toyotaPids();
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    loading.value = false;
  }
}

async function switchProfile(id: string): Promise<void> {
  if (switching.value || id === catalog.value?.profile.id) return;
  switching.value = true;
  try {
    await api.toyotaSelectPidProfile(id);
    selected.value = [];
    values.value = {};
    catalog.value = await api.toyotaPids();
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    switching.value = false;
  }
}

function toggle(id: number): void {
  const r = togglePid(selected.value, id);
  selected.value = r.selected;
  if (r.overLimit) toast(`最多同时读取 ${PID_SELECTION_LIMIT} 个数据项`, "warn");
}

// ---- 轮询 ----
let timer: ReturnType<typeof setInterval> | undefined;
let inflight = false;

async function readOnce(): Promise<void> {
  if (inflight || !selected.value.length) return;
  inflight = true;
  try {
    const next: Record<number, ToyotaPidValue> = {};
    for (const v of (await api.toyotaReadPids(selected.value)).values) next[v.id] = v;
    values.value = next;
  } catch (e) {
    toast(errorText(e), "error");
  } finally {
    inflight = false;
  }
}

const shouldPoll = computed(() => props.active && pageVisible.value && selected.value.length > 0);
function syncTimer(): void {
  if (shouldPoll.value && !timer) {
    void readOnce();
    timer = setInterval(() => void readOnce(), 1000);
  } else if (!shouldPoll.value && timer) {
    clearInterval(timer);
    timer = undefined;
  }
}
watch(shouldPoll, syncTimer);

watch(() => props.active, (a) => { if (a && !catalog.value) void loadCatalog(); }, { immediate: true });

const onVisibility = () => { pageVisible.value = document.visibilityState === "visible"; };
onMounted(() => document.addEventListener("visibilitychange", onVisibility));
onUnmounted(() => {
  document.removeEventListener("visibilitychange", onVisibility);
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="flex flex-col gap-3">
    <div v-if="loading && !catalog" class="grid h-24 place-items-center">
      <Loader2 class="size-6 animate-spin text-sl-text-3" />
    </div>

    <template v-if="catalog">
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-[13px] text-sl-text-3">数据配置</span>
        <Select
          :model-value="catalog.profile.id"
          :options="profileOptions"
          :pending="switching"
          aria-label="数据配置"
          @commit="switchProfile"
        />
        <div class="min-w-[160px] flex-1">
          <Input v-model="query" placeholder="搜索数据项名称" aria-label="搜索数据项" />
        </div>
        <span class="text-[13px] text-sl-text-3">已选 {{ selected.length }}/{{ PID_SELECTION_LIMIT }}</span>
      </div>

      <p v-if="!entries.length" class="sl-card px-5 py-6 text-center text-[13px] text-sl-text-3">
        没有匹配的数据项
      </p>
      <Accordion v-else :items="entries">
        <template #default="{ item: entry }">
          <ul class="divide-y divide-sl-border/70">
            <li v-for="p in pidsOf(entry.id)" :key="p.id" class="flex items-center gap-3 py-2 text-[13px]">
              <Checkbox
                :model-value="selected.includes(p.id)"
                :aria-label="`读取 ${p.name}`"
                @update:model-value="toggle(p.id)"
              />
              <span class="flex-1 text-sl-text-1">{{ p.name }}</span>
              <span class="text-sl-text-3">{{ p.unit }}</span>
            </li>
          </ul>
        </template>
      </Accordion>

      <section v-if="selectedPids.length" class="sl-card overflow-x-auto px-5 py-4">
        <table class="sl-tabular w-full text-left text-[13px]">
          <thead class="text-[11px] uppercase tracking-wider text-sl-text-3">
            <tr><th class="py-1 pr-3">名称</th><th class="py-1 pr-3 text-right">值</th><th class="py-1 pr-3">单位</th><th class="py-1">错误</th></tr>
          </thead>
          <tbody class="divide-y divide-sl-border/70">
            <tr v-for="p in selectedPids" :key="p.id">
              <td class="py-1.5 pr-3 text-sl-text-1">{{ p.name }}</td>
              <td class="py-1.5 pr-3 text-right font-mono text-sl-text-1">{{ values[p.id]?.text ?? "…" }}</td>
              <td class="py-1.5 pr-3 text-sl-text-3">{{ p.unit }}</td>
              <td class="py-1.5 text-sl-danger">{{ values[p.id]?.error }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>
