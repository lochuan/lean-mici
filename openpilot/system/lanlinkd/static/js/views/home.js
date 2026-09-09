// 首页：搜索框 + 状态摘要 + 面板卡片网格（对齐 sunnylink.ai 结构；Maps/Cruise 本 fork 不做）。
import { api } from "../api.js";
import { store, ensureData } from "../store.js";
import { esc } from "../components.js";

const NET = { 1: "WiFi", 2: "蜂窝2G", 3: "3G", 4: "4G", 5: "5G", 6: "以太网" };

export async function renderHome(app) {
  await ensureData();
  if (!store.status) { try { store.status = await api("/api/status"); } catch (_) { /* 首帧可缺省 */ } }
  const panels = (store.settings || {}).panels || [];
  const cards = panels
    .filter(p => p.id !== "models") // 模型面板并入 #/models 页
    .map(p => `<a class="nav-card" href="#/panel/${esc(p.id)}"><b>${esc(p.label || p.id)}</b>` +
      `<span>${esc(p.description || panelHint(p.id))}</span></a>`).join("");
  app.innerHTML = `
    <div style="position:relative">
      <input id="search" class="search" placeholder="搜索设置…" autocomplete="off">
      <div id="search-results" class="search-results" hidden></div>
    </div>
    <div class="summary">${summaryHTML(store.status)}</div>
    <a class="nav-card" href="#/models" style="margin-bottom:10px"><b>模型</b><span>浏览 / 搜索 / 切换驾驶模型</span></a>
    <div class="grid-nav">${cards}</div>`;
  bindSearch(app);
}

function panelHint(id) {
  return { steering: "MADS / 转向灯变道 / 扭矩", display: "亮度与超时", visuals: "HUD 元素与告警",
           toggles: "核心开关", device: "设备信息", software: "更新", developer: "网络与服务" }[id] || "";
}

function summaryHTML(s) {
  if (!s || !s.device) return "";
  const d = s.device;
  const chips = [
    ["温度", `${Math.max(...(d.cpuTempC.length ? d.cpuTempC : [0])).toFixed(0)}°C`],
    ["内存", `${d.memoryUsagePercent}%`],
    ["存储", `${d.freeSpacePercent.toFixed(0)}% 可用`],
    ["网络", NET[d.networkType] || "无"],
    ["车速", `${(s.car.vEgo * 3.6).toFixed(0)} km/h`],
    ["点火", s.system.ignition ? "ON" : "OFF"],
  ];
  return chips.map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
}

function buildIndex() {
  const idx = [];
  const push = (panel, node) => {
    for (const it of (node.items || [])) if (it.title || it.key) idx.push({ panel, key: it.key, title: it.title || it.key });
    for (const sp of (node.sub_panels || [])) push(panel, sp);
  };
  for (const p of (store.settings || {}).panels || []) for (const sec of p.sections || []) push(p, sec);
  for (const v of Object.values((store.settings || {}).vehicle_settings || {})) {
    push({ id: "vehicle", label: "车辆" }, v);
  }
  return idx;
}

function bindSearch(app) {
  const input = app.querySelector("#search");
  const box = app.querySelector("#search-results");
  const idx = buildIndex();
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) { box.hidden = true; return; }
    const hits = idx.filter(e => e.title.toLowerCase().includes(q)).slice(0, 8);
    box.innerHTML = hits.length
      ? hits.map(e => `<div data-goto="#/panel/${esc(e.panel.id)}" data-key="${esc(e.key)}">${esc(e.title)}<span>${esc(e.panel.label || e.panel.id)}</span></div>`).join("")
      : `<div class="empty">无匹配设置</div>`;
    box.hidden = false;
  });
  box.addEventListener("click", (e) => {
    const hit = e.target.closest("[data-goto]");
    if (!hit) return;
    box.hidden = true; input.value = "";
    location.hash = hit.dataset.goto;
    if (hit.dataset.key) flashItem(app, hit.dataset.key);
  });
  const onKey = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.focus(); }
  };
  document.addEventListener("keydown", onKey);
}

function flashItem(app, key) {
  // 目标行渲染完成后短暂高亮并滚动到位（等面板页 fetch 完成，重试若干次）
  let tries = 12;
  const tryFlash = () => {
    const el = app.querySelector(`[data-rowkey="${CSS.escape(key)}"]`);
    if (el) { el.classList.add("flash"); el.scrollIntoView({ block: "center" }); }
    else if (tries-- > 0) setTimeout(tryFlash, 120);
  };
  setTimeout(tryFlash, 150);
}
