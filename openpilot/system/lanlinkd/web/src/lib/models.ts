/** 模型列表的分组与排序。
 *
 * 规则取自车内 UI（ui/sunnypilot/mici/layouts/models.py）：
 *   - 按 bundle 的 folder override 分组
 *   - 组的顺序按组内 max(index) 倒序
 *   - 组内按 index 倒序（新模型在前）
 *   - "收藏"是个伪分组，置顶
 * sunnylink 的 /dashboard/models 呈现的就是这个结构。
 */
import type { ModelBundle } from "./schema";

export interface ModelGroup {
  folder: string;
  items: ModelBundle[];
  maxIndex: number;
}

export const FAVORITES_LABEL = "收藏";
export const UNGROUPED_LABEL = "其他模型";

export function searchBundles(bundles: ModelBundle[], query: string): ModelBundle[] {
  const q = query.trim().toLowerCase();
  if (!q) return bundles;
  return bundles.filter(
    (b) =>
      b.displayName.toLowerCase().includes(q) || b.internalName.toLowerCase().includes(q),
  );
}

export function groupBundles(bundles: ModelBundle[]): ModelGroup[] {
  const byFolder = new Map<string, ModelBundle[]>();
  for (const b of bundles) {
    // folder 缺失时给个兜底名，不能让分组标题是空白
    const key = b.folder || UNGROUPED_LABEL;
    const list = byFolder.get(key);
    if (list) list.push(b);
    else byFolder.set(key, [b]);
  }

  const groups: ModelGroup[] = [...byFolder.entries()].map(([folder, items]) => ({
    folder,
    items: [...items].sort((a, b) => b.index - a.index),
    maxIndex: Math.max(...items.map((i) => i.index)),
  }));
  groups.sort((a, b) => b.maxIndex - a.maxIndex || a.folder.localeCompare(b.folder));

  const favs = bundles.filter((b) => b.fav);
  if (favs.length) {
    groups.unshift({
      folder: FAVORITES_LABEL,
      items: [...favs].sort((a, b) => b.index - a.index),
      maxIndex: Number.POSITIVE_INFINITY,
    });
  }
  return groups;
}
