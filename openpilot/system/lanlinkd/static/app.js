// LANLink 单页应用。零依赖。
const $ = (id) => document.getElementById(id);
let token = localStorage.getItem("lanlink_token") || "";
let meta = {};
let statusTimer = null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (res.status === 401 && path !== "/api/login" && path !== "/api/setup") { showAuth(); throw new Error("unauthorized"); }
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

async function showAuth() {
  clearInterval(statusTimer); token = ""; localStorage.removeItem("lanlink_token");
  document.querySelectorAll(".tabview").forEach(v => v.hidden = true);
  $("tabs").hidden = true; $("view-auth").hidden = false; $("card-login").hidden = false;
  try {
    const probe = await fetch("/api/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    $("card-setup").hidden = probe.status === 409;   // 409 = 已设密码；400 = 未设（可设置）
  } catch (_) { $("card-setup").hidden = true; }
}

async function enter() {
  $("view-auth").hidden = true; $("tabs").hidden = false;
  switchTab("status");
  meta = await api("/api/params");
  renderParams("");
  clearInterval(statusTimer);
  statusTimer = setInterval(pollStatus, 5000);
  pollStatus();
}

// ---------- tabs ----------
function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tabview").forEach(v => v.hidden = v.id !== `view-${name}`);
  if (name === "params") renderParams($("param-search").value);
  if (name === "settings") renderSettings();
  if (name === "logs") renderLogs();
}

// ---------- status ----------
const NET = { 1: "WiFi", 2: "蜂窝2G", 3: "3G", 4: "4G", 5: "5G", 6: "以太网" };
const THERMAL = { 0: "正常", 1: "轻载", 2: "中载", 3: "重载", 4: "过热" };
let lastParamsVersion = null;
let paramsVersionPrimed = false;

async function pollStatus() {
  try {
    const s = await api("/api/status");
    $("conn").textContent = `已连接 · ${s.system.version} @ ${s.system.branch}`;
    if (s.paramsVersion !== undefined && s.paramsVersion !== lastParamsVersion) {
      // 车机端改了参数 → 刷新本页缓存，设置页改动可见（首轮只记录基线）
      const first = !paramsVersionPrimed;
      lastParamsVersion = s.paramsVersion;
      paramsVersionPrimed = true;
      if (!first) {
        paramsAll = await api("/api/params/_all");
        if (!$("view-settings").hidden) renderSettings();
      }
    }
    const c = s.capabilities || {};
    const cards = [
      ["温度", `${Math.max(...(s.device.cpuTempC.length ? s.device.cpuTempC : [0])).toFixed(1)}°C`],
      ["内存", `${s.device.memoryUsagePercent}%`],
      ["存储", `${s.device.freeSpacePercent.toFixed(0)}% 可用`],
      ["供电", s.device.usbOnline ? "USB" : "电池"],
      ["网络", NET[s.device.networkType] || "未知"],
      ["温控", THERMAL[s.device.thermalStatus] || "未知"],
      ["车速", `${(s.car.vEgo * 3.6).toFixed(0)} km/h`],
      ["挡位", s.car.standstill ? "停稳" : "行驶"],
      ["转向角", `${s.car.steeringAngleDeg.toFixed(1)}°`],
      ["BSM", c.enable_bsm ? `左${s.car.leftBlindspot ? "!" : "−"} 右${s.car.rightBlindspot ? "!" : "−"}` : "无"],
      ["点火", s.system.ignition ? "ON" : "OFF"],
      ["GPS", `${s.gps.satelliteCount} 星`],
      ["位置", `${s.gps.latitude.toFixed(5)}, ${s.gps.longitude.toFixed(5)}`],
    ];
    $("view-status").innerHTML = `<div class="grid">` + cards.map(
      ([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("") + `</div>`;
  } catch (e) { $("conn").textContent = "连接失败"; }
}

// ---------- params ----------
function renderParams(filter) {
  const keys = Object.keys(meta).filter(k => !meta[k].blocked && k.toLowerCase().includes((filter || "").toLowerCase())).sort();
  $("param-list").innerHTML = keys.map(k => `
    <div class="row" onclick="editParam('${k}')">
      <span class="k">${k}</span>
      <span class="badge">${meta[k].type}${meta[k]._missing ? " · 未注册" : ""}</span>
    </div>`).join("") || "<p class='desc'>无匹配</p>";
}

async function editParam(key) {
  const { value } = await api(`/api/params/${key}`);
  const m = meta[key];
  const body = m.type === "BOOL"
    ? `<button class="switch ${value === "1" ? "on" : ""}" id="pv" onclick="this.classList.toggle('on')"></button>`
    : `<textarea id="pv" rows="4">${value ?? ""}</textarea>`;
  $("view-params").innerHTML = `
    <div class="card"><h3>${key}</h3>${body}
    <p class="desc">类型 ${m.type}；修改立即写入（部分参数需重启 openpilot 生效）</p>
    <button onclick="saveParam('${key}')">保存</button>
    <button class="danger" onclick="delParam('${key}')">删除</button>
    <button onclick="renderParams($('param-search').value)">返回</button></div>`;
}

async function saveParam(key) {
  let v;
  if (meta[key].type === "BOOL") v = $("pv").classList.contains("on") ? "1" : "0";
  else v = $("pv").value;
  await api(`/api/params/${key}`, { method: "PUT", body: JSON.stringify({ value: v }) });
  renderParams($("param-search").value);
}

async function delParam(key) {
  if (!confirm(`确认删除 ${key}？`)) return;
  await api(`/api/params/${key}`, { method: "DELETE" });
  renderParams($("param-search").value);
}

// ---------- settings ----------
let settingsCache = null, caps = null, paramsAll = null;

async function renderSettings() {
  if (!settingsCache) settingsCache = await api("/api/settings_ui");
  if (!caps) caps = await api("/api/capabilities");
  if (!paramsAll) paramsAll = await api("/api/params/_all");
  const brand = (caps || {}).brand;
  const vehicle = (settingsCache.vehicle_settings || {})[brand];
  $("view-settings").innerHTML = `<div id="settings-pages">` +
    (settingsCache.panels || []).map(panel =>
      `<h3 class="sep">${panel.label || panel.id}</h3>` +
      (panel.sections || []).map(sec => renderSection(sec)).join("") +
      (panel.sub_panels || []).map(sp => renderSubPanel(sp, true)).join("")
    ).join("") +
    (vehicle ? renderSection({ title: vehicle.title, description: vehicle.description, items: vehicle.items }) : "") +
    `</div>`;
}

function ruleOk(rules) {
  // enablement/visibility 规则求值。offroad_only/not_engaged 本地不限制（写入侧由设备自身校验）
  return (rules || []).every(r => {
    if (!r || typeof r !== "object") { console.warn("未知规则，按可用处理:", r); return true; }
    switch (r.type) {
      case "offroad_only":
      case "not_engaged":
        return true;
      case "capability":
        return caps[r.field] === r.equals;
      case "param": {
        const norm = (v) => (v === true ? "1" : v === false ? "0" : String(v));
        const actual = norm(paramsAll[r.key]);
        const expected = { True: "1", true: "1", False: "0", false: "0" }[norm(r.equals)] ?? norm(r.equals);
        return actual === expected;
      }
      case "param_compare": {
        const v = Number(paramsAll[r.key]);
        if (Number.isNaN(v)) return false;
        switch (r.op) {
          case "<": return v < r.value;
          case "<=": return v <= r.value;
          case ">": return v > r.value;
          case ">=": return v >= r.value;
          case "==": return v === r.value;
          case "!=": return v !== r.value;
          default: console.warn("未知比较符，按可用处理:", r); return true;
        }
      }
      case "any": return (r.conditions || []).some(c => ruleOk([c]));
      case "all": return (r.conditions || []).every(c => ruleOk([c]));
      case "not": return r.condition ? !ruleOk([r.condition]) : true;
      default:
        console.warn("未知规则，按可用处理:", r);
        return true;
    }
  });
}

function renderSection(sec) {
  if (sec.visibility && !ruleOk(sec.visibility)) return "";
  const secEnabled = ruleOk(sec.enablement);
  const rows = renderItems(sec.items, secEnabled);
  const subs = (sec.sub_panels || []).map(sp => renderSubPanel(sp, secEnabled)).join("");
  return (sec.title ? `<h3 class="sep">${sec.title}</h3>` : "") + rows + subs +
    (sec.description ? `<p class="desc">${sec.description}</p>` : "");
}

function renderItems(items, secEnabled) {
  return (items || []).map(item => {
    const key = item.key, m = meta[key] || {};
    if (!ruleOk(item.visibility)) return "";
    if (m.blocked || item.blocked) return "";
    const missing = item._missing || m._missing;
    const value = paramsAll[key];
    const enabled = secEnabled && ruleOk(item.enablement) && !missing;
    let control = "";
    if (item.widget === "toggle") {
      control = `<div class="switch ${String(value) === "1" ? "on" : ""}" ${enabled ? "" : "hidden"} onclick="flipSetting('${key}', this)"></div>`;
    } else if (item.widget === "option" && (item.choices || item.options)) {
      const opts = item.choices || item.options;
      control = `<select ${enabled ? "" : "disabled"} onchange="putSetting('${key}', this.value)">` +
        opts.map(o => `<option value="${o.value ?? o}" ${String(value) === String(o.value ?? o) ? "selected" : ""}>${o.label ?? o}</option>`).join("") + `</select>`;
    } else if (item.widget === "info") {
      control = `<span class="badge">${value ?? ""}</span>`;
    } else { // multiple_button / button：v1 灰显占位
      control = `<span class="badge">车机端设置</span>`;
    }
    return `<div class="row ${enabled ? "" : "disabled"}" style="cursor:default">
        <div style="flex:1"><div class="k">${item.title || key}</div>
        ${item.description ? `<div class="desc">${item.description}</div>` : ""}</div>${control}</div>`;
  }).join("");
}

function renderSubPanel(sp, secEnabled) {
  // 上游 schema：SubPanel 由 trigger_key/trigger_condition 触发才显示；无条件恒显
  if (sp.trigger_condition && !ruleOk([sp.trigger_condition])) return "";
  return `<div class="sub-panel"><div class="sub-label">${sp.label || sp.id}</div>` +
    renderItems(sp.items, secEnabled) + `</div>`;
}

async function flipSetting(key, el) {
  const nv = el.classList.contains("on") ? "0" : "1";
  await putSetting(key, nv);
  el.classList.toggle("on", nv === "1");
}

function findItem(key) {
  // 在 panels（sections + sub_panels）+ vehicle_settings 里查找设置项
  const walkNode = (node) => {
    for (const item of (node.items || [])) if (item.key === key) return item;
    for (const sub of (node.sub_panels || [])) { const hit = walkNode(sub); if (hit) return hit; }
    return null;
  };
  for (const panel of ((settingsCache || {}).panels || [])) {
    for (const sec of (panel.sections || [])) { const hit = walkNode(sec); if (hit) return hit; }
    for (const sp of (panel.sub_panels || [])) { const hit = walkNode(sp); if (hit) return hit; }
  }
  for (const brand of Object.values((settingsCache || {}).vehicle_settings || {})) {
    const hit = walkNode(brand);
    if (hit) return hit;
  }
  return null;
}

async function putSetting(key, value) {
  try {
    await api(`/api/params/${key}`, { method: "PUT", body: JSON.stringify({ value: String(value) }) });
    paramsAll[key] = String(value);
    const item = findItem(key);
    if (item && item.needs_onroad_cycle) alert("已保存，重启 openpilot 后生效");
  } catch (e) { alert(e.message); }
}

// ---------- logs ----------
async function downloadLog(route) {
  try {
    // <a href> 无法带 Bearer 头 → fetch+blob 触发保存
    const res = await fetch(`/api/logs/${route}/1/qlog.zst`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "qlog.zst";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  } catch (e) { alert(`下载失败: ${e.message}`); }
}

async function renderLogs() {
  const routes = await api("/api/logs");
  $("view-logs").innerHTML = routes.map(r => `
    <div class="row"><div style="flex:1">
      <div class="k">${r.route}</div>
      <div class="desc">${r.segments} 段 · ${(r.size / 1048576).toFixed(1)} MB · ${new Date(r.mtime * 1000).toLocaleString()}</div>
      <div class="desc"><a href="javascript:void(0)" onclick="downloadLog('${r.route}')">qlog.zst</a></div>
    </div></div>`).join("") || "<p class='desc'>暂无日志</p>";
}

// ---------- events ----------
$("login-btn").onclick = async () => {
  try {
    const { token: t } = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("login-pw").value }) });
    token = t; localStorage.setItem("lanlink_token", t);
    await enter();
  } catch (e) { $("auth-msg").textContent = e.message; }
};
$("setup-btn").onclick = async () => {
  if ($("setup-pw1").value !== $("setup-pw2").value || $("setup-pw1").value.length < 6) { $("auth-msg").textContent = "两次密码不一致或长度不足"; return; }
  try {
    await api("/api/setup", { method: "POST", body: JSON.stringify({ password: $("setup-pw1").value }) });
    const { token: t } = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("setup-pw1").value }) });
    token = t; localStorage.setItem("lanlink_token", t);
    await enter();
  } catch (e) { $("auth-msg").textContent = e.message; }
};
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
$("param-search").oninput = () => renderParams($("param-search").value);

async function boot() {
  if (token) {
    try { await api("/api/params"); return enter(); } catch (_) { if (token) showAuth(); return; }
  }
  showAuth();
}
boot();
