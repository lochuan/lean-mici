<script setup lang="ts">
/** 模型管理页。对齐 sunnylink /dashboard/models 的结构（实地勘察 2026-09-12）：
 *
 *   Active Models           当前模型卡片 + Clear Models Cache
 *   Available Models        「小模型」标签 + 搜索框 + 按 folder 折叠分组
 *   （随后是 Models 面板的普通设置项，由 PanelView 接着渲染）
 *
 * 分组依据是 bundle 的 folder override，与车内 UI 一致
 * （ui/sunnypilot/mici/layouts/models.py：按 max(index) 倒序，收藏置顶）。
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ChevronRight, Download, Loader2, RefreshCw, Star, Trash2, X } from "lucide-vue-next";
import Button from "./ui/Button.vue";
import Badge from "./ui/Badge.vue";
import { api } from "@/lib/api";
import { loadModels, store, toast } from "@/lib/store";
import { cn } from "@/lib/utils";
import { groupBundles, searchBundles } from "@/lib/models";
import type { ModelBundle } from "@/lib/schema";

const busy = ref("");
const query = ref("");
const openFolders = ref<Set<string>>(new Set());
let timer: ReturnType<typeof setInterval> | undefined;

const state = computed(() => store.models);
const download = computed(() => state.value?.download ?? null);

/** 下载中：轮询进度。车机下载模型要几十秒到几分钟。 */
const downloading = computed(() => Boolean(download.value?.status === "downloading" || state.value?.queued_ref));

const filtered = computed(() => searchBundles(state.value?.bundles ?? [], query.value));

/** 分组与排序规则见 lib/models.ts（对齐车内 UI 与 sunnylink） */
const groups = computed(() => groupBundles(filtered.value));

function toggleFolder(f: string): void {
  const next = new Set(openFolders.value);
  if (next.has(f)) next.delete(f);
  else next.add(f);
  openFolders.value = next;
}

async function act(name: string, fn: () => Promise<unknown>, ok: string): Promise<void> {
  busy.value = name;
  try {
    await fn();
    await loadModels();
    toast(ok, "ok");
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), "error");
  } finally {
    busy.value = "";
  }
}

const select = (b: ModelBundle) =>
  act(b.ref, () => api.selectModel(b.ref), `已开始下载「${b.displayName}」`);
const useDefault = () =>
  act("default", () => api.selectModel("Default"), "已切回默认模型");
const cancel = () => act("cancel", () => api.cancelModel(), "已取消下载");
const refresh = () => act("refresh", () => api.refreshModels(), "已刷新模型列表");
const clearCache = () => act("clear", () => api.clearModelCache(), "已清空模型缓存");
const toggleFav = (b: ModelBundle) =>
  act(`fav-${b.ref}`, () => api.favModel(b.ref, !b.fav), b.fav ? "已取消收藏" : "已收藏");

onMounted(async () => {
  try {
    await loadModels();
  } catch {
    toast("读取模型列表失败", "error");
  }
  // 下载进度需要更勤的轮询；没有下载时 2s 一次开销也可忽略（局域网）
  timer = setInterval(() => {
    if (downloading.value) void loadModels().catch(() => {});
  }, 2000);
});

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="flex flex-col gap-4">
    <!-- 当前模型 -->
    <section class="sl-card px-5 py-4">
      <div class="mb-3 flex items-center justify-between gap-3">
        <h2 class="text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
          当前模型
        </h2>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="grid size-7 place-items-center rounded-md text-sl-text-3 transition-colors hover:bg-sl-surface-3 hover:text-sl-text-1 disabled:opacity-40"
            :disabled="busy === 'refresh'"
            title="刷新模型列表"
            aria-label="刷新"
            @click="refresh"
          >
            <Loader2 v-if="busy === 'refresh'" class="size-3.5 animate-spin" />
            <RefreshCw v-else class="size-3.5" />
          </button>
          <!-- 复刻实测的小号描边按钮（11px / 24px 高 / 圆角 4px） -->
          <button
            type="button"
            class="flex h-6 items-center gap-1.5 rounded px-2 text-[11px] font-semibold text-sl-text-2 ring-1 ring-inset ring-sl-border transition-colors hover:bg-sl-surface-3 hover:text-sl-text-1 disabled:opacity-40"
            :disabled="busy === 'clear'"
            @click="clearCache"
          >
            <Trash2 class="size-3" />
            清空模型缓存
            <span v-if="state?.cache_size_mb" class="text-sl-text-3">
              {{ state.cache_size_mb }} MB
            </span>
          </button>
        </div>
      </div>

      <div class="flex items-center justify-between gap-3 rounded-lg bg-sl-bg px-4 py-3 ring-1 ring-inset ring-sl-border">
        <div class="min-w-0">
          <div class="text-[10px] font-semibold uppercase tracking-wider text-sl-accent">
            正在使用
          </div>
          <div class="mt-0.5 truncate text-[15px] text-sl-text-1">
            {{ state?.active?.displayName || state?.default_model || "默认模型" }}
          </div>
          <div v-if="state?.active" class="mt-0.5 flex items-center gap-2 text-[11px] text-sl-text-3">
            <span class="sl-tabular">{{ state.active.internalName }}</span>
            <span>·</span>
            <span>{{ state.active.runner }}</span>
          </div>
          <div v-else class="mt-0.5 text-[11px] text-sl-text-3">未选择自定义模型</div>
        </div>
        <Button
          v-if="state?.active"
          size="sm"
          variant="ghost"
          :disabled="busy === 'default'"
          @click="useDefault"
        >
          切回默认
        </Button>
      </div>

      <!-- 下载进度 -->
      <div
        v-if="downloading"
        class="mt-3 rounded-lg bg-sl-accent/8 px-4 py-3 ring-1 ring-inset ring-sl-accent/25"
      >
        <div class="flex items-center justify-between gap-3">
          <span class="flex items-center gap-2 text-[13px] text-sl-text-1">
            <Download class="size-3.5 animate-pulse text-sl-accent" />
            正在下载模型…
            <span v-if="download?.progress !== undefined" class="sl-tabular text-sl-accent">
              {{ Math.round(download.progress) }}%
            </span>
          </span>
          <Button size="sm" variant="ghost" :disabled="busy === 'cancel'" @click="cancel">
            <X class="size-3.5" />
            取消
          </Button>
        </div>
        <div class="mt-2 h-1 overflow-hidden rounded-full bg-sl-surface-3">
          <div
            class="h-full rounded-full bg-sl-accent transition-[width] duration-500"
            :style="{ width: `${download?.progress ?? 0}%` }"
          />
        </div>
      </div>
    </section>

    <!-- 可用模型 -->
    <section class="sl-card px-5 py-4">
      <h2 class="mb-1 text-[13px] font-semibold uppercase tracking-wider text-sl-text-3">
        可用模型
      </h2>
      <div class="mb-3 flex items-center gap-2">
        <Badge kind="muted">小模型</Badge>
        <span class="text-[12px] text-sl-text-3">{{ state?.bundles?.length ?? 0 }} 个模型</span>
      </div>

      <input
        v-model="query"
        type="text"
        placeholder="搜索模型…"
        class="mb-2 h-10 w-full rounded-lg bg-sl-bg px-3 text-sm text-sl-text-1 outline-none ring-1 ring-inset ring-sl-border transition-shadow placeholder:text-sl-text-3 focus:ring-2 focus:ring-sl-accent"
      />

      <p v-if="!groups.length" class="py-6 text-center text-[13px] text-sl-text-3">
        {{ query ? `没有匹配「${query}」的模型` : "暂无可用模型，请先刷新列表" }}
      </p>

      <div v-else class="flex flex-col">
        <div v-for="g in groups" :key="g.folder" class="border-b border-sl-border/60 last:border-0">
          <!-- 分组头：48px，与实测一致 -->
          <button
            type="button"
            class="flex h-12 w-full items-center justify-between gap-2 text-left transition-colors hover:bg-sl-surface-2/60"
            @click="toggleFolder(g.folder)"
          >
            <span class="flex items-center gap-2">
              <ChevronRight
                :class="
                  cn(
                    'size-4 shrink-0 text-sl-text-3 transition-transform',
                    openFolders.has(g.folder) && 'rotate-90',
                  )
                "
              />
              <span class="text-sm text-sl-text-1">{{ g.folder }}</span>
            </span>
            <span class="sl-tabular pr-1 text-[12px] text-sl-text-3">{{ g.items.length }}</span>
          </button>

          <!-- 模型行：54px，左缩进 44px（实测） -->
          <div v-if="openFolders.has(g.folder)" class="flex flex-col pb-1">
            <div
              v-for="b in g.items"
              :key="b.ref"
              :class="
                cn(
                  'flex h-[54px] items-center justify-between gap-3 rounded-lg pl-11 pr-2 transition-colors',
                  b.active ? 'bg-sl-accent/8' : 'hover:bg-sl-surface-2',
                )
              "
            >
              <button
                type="button"
                class="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default"
                :disabled="b.active || Boolean(busy)"
                :title="b.active ? '当前已在使用' : `切换到「${b.displayName}」`"
                @click="select(b)"
              >
                <Loader2 v-if="busy === b.ref" class="size-3.5 shrink-0 animate-spin text-sl-accent" />
                <span class="min-w-0">
                  <span class="block truncate text-[14px] text-sl-text-1">{{ b.displayName }}</span>
                  <span class="flex items-center gap-1.5 text-[11px] text-sl-text-3">
                    <span class="sl-tabular">{{ b.internalName }}</span>
                    <span v-if="b.is20hz" class="text-sl-info">20Hz</span>
                    <span v-if="b.environment !== 'release'">{{ b.environment }}</span>
                  </span>
                </span>
                <Badge v-if="b.active" kind="accent">使用中</Badge>
              </button>

              <button
                type="button"
                class="grid size-[26px] shrink-0 place-items-center rounded-md transition-colors hover:bg-sl-surface-3 disabled:opacity-40"
                :disabled="busy === `fav-${b.ref}`"
                :title="b.fav ? '取消收藏' : '收藏'"
                :aria-label="b.fav ? '取消收藏' : '收藏'"
                @click="toggleFav(b)"
              >
                <Star
                  :class="
                    cn('size-4', b.fav ? 'fill-sl-warn text-sl-warn' : 'text-sl-text-3')
                  "
                />
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
