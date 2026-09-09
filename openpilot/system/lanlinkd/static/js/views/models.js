// 模型页：当前模型卡 / 下载卡 / 操作行 / 搜索 / 分组列表。
// 轮询只局部更新（mm-live/model-list），不重渲染骨架——避免打断输入与折叠状态。
import { api } from "../api.js";
import { esc, toast } from "../components.js";
import { setCleanup } from "../router.js";

const STATUS_TEXT = {
  downloading: "下载中", verifying: "校验中", downloaded: "已下载",
  cached: "已缓存", failed: "下载失败", notdownloading: "",
};
const RUNNER_TEXT = { snpe: "SNPE", tinygrad: "tinygrad", stock: "stock" };

let timer = null;
let bound = false;
let filter = "";
let favOnly = false;
const folded = new Set();

export async function renderModels(app) {
  setCleanup(() => { clearInterval(timer); timer = null; });
  if (!bound) {
    app.addEventListener("click", modelsClick);
    app.addEventListener("change", modelsChange);
    document.addEventListener("input", modelSearchInput);
    bound = true;
  }
  app.innerHTML = `<div id="models-view"><div class="empty-state">加载中…</div></div>`;
  try {
    await repaint(true);
  } catch (e) { toast(e.message, true); }
  if (!timer) {
    timer = setInterval(async () => {
      try { await repaint(false); } catch (_) { /* 单次轮询失败静默，下轮重试 */ }
    }, 2000);
  }
}

async function repaint(full) {
  const m = await api("/api/models");
  const view = document.getElementById("models-view");
  if (!view) return; // 已离开页面
  if (full) view.innerHTML = skeleton(m);
  const live = document.getElementById("mm-live");
  if (live) live.innerHTML = liveHTML(m);
  const list = document.getElementById("model-list");
  if (list) list.innerHTML = listHTML(m);
}

function skeleton(m) {
  return `
  <div class="panel-head"><a class="back" href="#/">← 返回</a><h2>模型</h2></div>
  <div id="mm-live"></div>
  <div class="mm-actions">
    <button data-act="refresh">刷新列表</button>
    <button data-act="clear">清空缓存</button>
    <label style="align-self:center;color:var(--dim);font-size:13px">
      <input type="checkbox" id="fav-only" data-act="favonly" style="width:auto;margin:0 4px 0 0" ${favOnly ? "checked" : ""}>只看收藏
    </label>
    <span style="align-self:center;color:var(--dim);font-size:12px;margin-left:auto">缓存 ${m.cache_size_mb} MB</span>
  </div>
  <input id="model-search" class="search" placeholder="搜索模型…" value="${esc(filter)}" autocomplete="off">
  <div id="model-list"></div>`;
}

function liveHTML(m) {
  const activeName = m.active ? m.active.displayName : `${m.default_model}（默认）`;
  const queuedName = m.queued_ref && (!m.active || m.queued_ref !== m.active.ref)
    ? (nameByRef(m, m.queued_ref) || m.queued_ref) : null;
  const dl = m.download && ["downloading", "verifying", "failed"].includes(m.download.status) ? m.download : null;
  return `
  <div class="mm-active">
    <div class="k" style="color:var(--dim);font-size:12px">当前模型</div>
    <div class="name">${esc(activeName)}</div>
    <div class="mm-tags">
      ${m.active ? tag(RUNNER_TEXT[m.active.runner] || m.active.runner) : ""}
      ${queuedName ? tag(`已排队：${queuedName}`) : ""}
    </div>
    <div class="mm-actions">
      <button data-act="default">恢复默认</button>
    </div>
  </div>
  ${dl ? downloadHTML(dl) : ""}`;
}

function tag(t) { return `<span class="badge">${esc(t)}</span>`; }

function nameByRef(m, ref) {
  const b = (m.bundles || []).find(x => x.ref === ref);
  return b ? b.displayName : null;
}

function downloadHTML(dl) {
  const failed = dl.status === "failed";
  return `<div class="mm-download">
    <div class="k" style="color:var(--dim);font-size:12px">${esc(dl.displayName || dl.internalName || "模型")}</div>
    ${failed
      ? `<div style="color:var(--err);font-weight:600">下载失败</div>`
      : `<div class="progress ${dl.verifying ? "stalled" : ""}"><div style="width:${Math.min(100, dl.progress)}%"></div></div>
         <div style="color:var(--dim);font-size:12px">${STATUS_TEXT[dl.status] || dl.status} · ${dl.progress.toFixed(1)}%${dl.eta ? ` · 剩余 ${dl.eta}s` : ""}</div>`}
    <div class="mm-actions">
      ${failed ? `<button data-act="retry" data-ref="${esc(dl.ref)}" class="primary">重试</button>` : ""}
      <button data-act="cancel" class="${failed ? "" : "danger"}">取消</button>
    </div>
  </div>`;
}

function listHTML(m) {
  let list = m.bundles || [];
  if (filter) list = list.filter(b => b.displayName.toLowerCase().includes(filter.toLowerCase()));
  if (favOnly) list = list.filter(b => b.fav);
  if (!list.length) return `<div class="empty-state">暂无模型列表，点“刷新列表”获取（设备需联网）</div>`;
  const folders = {};
  for (const b of list) (folders[b.folder || "其他"] ||= []).push(b);
  const maxIdx = arr => Math.max(...arr.map(b => b.index), -1);
  const names = Object.keys(folders).sort((a, b) => maxIdx(folders[b]) - maxIdx(folders[a]));
  return names.map(folder => {
    // fav 置顶于各自分组
    const arr = [...folders[folder]].sort((a, b) => (Number(b.fav) - Number(a.fav)) || (b.index - a.index));
    return `<div class="mm-group">
      <button data-act="fold" data-folder="${esc(folder)}">${esc(folder)} <span class="count">${arr.length}</span></button>
      <div class="mm-group-items" ${folded.has(folder) ? "hidden" : ""}>${arr.map(rowHTML).join("")}</div>
    </div>`;
  }).join("");
}

function rowHTML(b) {
  return `<div class="mm-row ${b.active ? "active" : ""}" data-ref="${esc(b.ref)}" data-modelname="${esc(b.displayName)}">
    <div class="nm">${esc(b.displayName)}
      <small>${esc(RUNNER_TEXT[b.runner] || b.runner)}${b.generation ? ` · gen ${esc(b.generation)}` : ""}${b.is20hz ? " · 20Hz" : ""}</small>
    </div>
    <button class="star ${b.fav ? "on" : ""}" data-act="fav" data-ref="${esc(b.ref)}" data-on="${b.fav ? "0" : "1"}">★</button>
  </div>`;
}

function modelsClick(e) {
  const view = document.getElementById("models-view");
  if (!view) return;
  const star = e.target.closest('[data-act="fav"]');
  if (star) {
    e.stopPropagation();
    api("/api/models/fav", { method: "POST", body: JSON.stringify({ ref: star.dataset.ref, on: star.dataset.on === "1" }) })
      .then(() => repaint(false)).catch(err => toast(err.message, true));
    return;
  }
  const fold = e.target.closest('[data-act="fold"]');
  if (fold) {
    const f = fold.dataset.folder;
    if (folded.has(f)) folded.delete(f); else folded.add(f);
    const items = fold.parentElement.querySelector(".mm-group-items");
    if (items) items.hidden = !items.hidden;
    return;
  }
  const act = e.target.closest("[data-act]");
  if (!act) return;
  const a = act.dataset.act;
  if (a === "default") {
    if (confirm("恢复默认模型？当前所选模型将被停用。")) {
      api("/api/models/select", { method: "POST", body: JSON.stringify({ ref: "Default" }) })
        .then(() => { toast("已恢复默认，下载完成后自动激活"); repaint(false); })
        .catch(err => toast(err.message, true));
    }
  } else if (a === "refresh") {
    api("/api/models/refresh", { method: "POST", body: "{}" })
      .then(() => toast("已请求刷新，稍后自动更新")).catch(err => toast(err.message, true));
  } else if (a === "clear") {
    if (confirm("清空模型缓存？保留当前激活模型的文件。")) {
      api("/api/models/clear_cache", { method: "POST", body: "{}" })
        .then(() => { toast("已请求清空缓存"); repaint(false); })
        .catch(err => toast(err.message, true));
    }
  } else if (a === "cancel") {
    api("/api/models/cancel", { method: "POST", body: "{}" })
      .then(() => { toast("已取消下载"); repaint(false); })
      .catch(err => toast(err.message, true));
  } else if (a === "retry") {
    api("/api/models/select", { method: "POST", body: JSON.stringify({ ref: act.dataset.ref }) })
      .then(() => repaint(false)).catch(err => toast(err.message, true));
  } else {
    const row = act.closest(".mm-row");
    if (row && !row.classList.contains("active")) {
      if (confirm(`切换到「${row.dataset.modelname}」？将自动下载并激活。`)) {
        api("/api/models/select", { method: "POST", body: JSON.stringify({ ref: row.dataset.ref }) })
          .then(() => { toast("已排队下载，完成后自动激活"); repaint(false); })
          .catch(err => toast(err.message, true));
      }
    }
  }
}

function modelsChange(e) {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  if (el.dataset.act === "favonly") { favOnly = el.checked; repaint(false).catch(err => toast(err.message, true)); }
}

function modelSearchInput(e) {
  if (!e.target || e.target.id !== "model-search") return;
  filter = e.target.value;
  // 局部刷新列表，不动搜索框（保焦点）
  api("/api/models").then(m => {
    const list = document.getElementById("model-list");
    if (list) list.innerHTML = listHTML(m);
  }).catch(() => {});
}
