/** TechStream 的页面栈：首页 → 功能页 → 子页。切换页面时清掉上一页留下的 toast。 */
import { computed, ref } from "vue";
import { clearToasts } from "./store";

export type ToyotaPageName =
  | "home" | "custom" | "customCat" | "dtc" | "dtcEcu" | "live" | "liveTool" | "battery" | "backups";

export interface ToyotaPageEntry {
  page: ToyotaPageName;
  /** 子页的参数：分类 id / ECU id / 实时数据主题 id */
  arg?: string;
}

const stack = ref<ToyotaPageEntry[]>([{ page: "home" }]);

export const currentPage = computed(() => stack.value[stack.value.length - 1]);
export const canGoBack = computed(() => stack.value.length > 1);

export function goTo(page: ToyotaPageName, arg?: string): void {
  clearToasts();
  stack.value.push({ page, arg });
  window.scrollTo(0, 0);
}

export function goBack(): void {
  if (stack.value.length <= 1) return;
  clearToasts();
  stack.value.pop();
}

export function resetToHome(): void {
  clearToasts();
  stack.value = [{ page: "home" }];
}
