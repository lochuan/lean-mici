// 全局数据缓存：settings_ui/capabilities/params 全量。paramsVersion 变化时刷新。
import { api } from "./api.js";

export const store = {
  meta: {}, caps: {}, paramsAll: {}, settings: null, status: null,
  paramsVersion: null, primed: false, loaded: false,
};

export async function ensureData() {
  if (store.loaded) return;
  const [meta, caps, paramsAll, settings] = await Promise.all([
    api("/api/params"), api("/api/capabilities"), api("/api/params/_all"), api("/api/settings_ui"),
  ]);
  Object.assign(store, { meta, caps, paramsAll, settings, loaded: true });
}

export async function refreshParams() {
  // 车机端改动跟随：params 与 capabilities 一并刷新（修掉旧版 caps 永不刷新）
  [store.paramsAll, store.caps] = await Promise.all([api("/api/params/_all"), api("/api/capabilities")]);
}

export function findItem(key) {
  const walkNode = (node) => {
    for (const item of (node.items || [])) if (item.key === key) return item;
    for (const sub of (node.sub_panels || [])) { const hit = walkNode(sub); if (hit) return hit; }
    return null;
  };
  for (const panel of ((store.settings || {}).panels || [])) {
    for (const sec of (panel.sections || [])) { const hit = walkNode(sec); if (hit) return hit; }
    for (const sp of (panel.sub_panels || [])) { const hit = walkNode(sp); if (hit) return hit; }
  }
  for (const brand of Object.values((store.settings || {}).vehicle_settings || {})) {
    const hit = walkNode(brand);
    if (hit) return hit;
  }
  return null;
}
