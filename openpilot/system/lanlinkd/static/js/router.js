// hash 路由：#/ → 首页，#/panel/{id} → 面板页，#/models → 模型页。
import { renderHome } from "./views/home.js";
import { renderPanel } from "./views/panel.js";
import { renderModels } from "./views/models.js";

let current = null;
let cleanupFn = null;

export function setCleanup(fn) { cleanupFn = fn; }

export async function route(app) {
  if (cleanupFn) { try { cleanupFn(); } catch (_) { /* noop */ } cleanupFn = null; }
  const h = location.hash || "#/";
  if (h === "#/models") current = () => renderModels(app);
  else if (/^#\/panel\/([\w-]+)$/.test(h)) {
    const id = h.slice("#/panel/".length);
    current = () => renderPanel(app, id);
  } else current = () => renderHome(app);
  return current();
}

export function rerender() { if (current) return current(); }
