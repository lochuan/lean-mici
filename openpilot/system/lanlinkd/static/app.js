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
  clearInterval(statusTimer);
  statusTimer = setInterval(pollStatus, 5000);
  pollStatus();
}

// ---------- tabs ----------
function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tabview").forEach(v => v.hidden = v.id !== `view-${name}`);
  if (name === "settings") renderSettings();
}

// ---------- status ----------
const NET = { 1: "WiFi", 2: "蜂窝2G", 3: "3G", 4: "4G", 5: "5G", 6: "以太网" };
const THERMAL = { 0: "正常", 1: "轻载", 2: "中载", 3: "重载", 4: "过热" };
let lastParamsVersion = null;
let paramsVersionPrimed = false;

async function pollStatus() {
  try {
    const s = await api("/api/status");
    $("conn").textContent = `已连接 · ${s.system.version} @ ${s.system.branch} ${s.system.commit || ""}`;
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
  return (sec.title ? `<h3 class="sep">${sec.title}</h3>` : "") + rows + subs;
}

function renderItems(items, secEnabled) {
  const out = [];
  const emit = (item, isSub) => {
    const key = item.key, m = meta[key] || {};
    if (!ruleOk(item.visibility)) return;
    if (m.blocked || item.blocked) return;
    const missing = item._missing || m._missing;
    const value = paramsAll[key];
    const enabled = secEnabled && ruleOk(item.enablement) && !missing;
    let control = "";
    if (item.widget === "toggle") {
      control = `<div class="switch ${String(value) === "1" ? "on" : ""}" ${enabled ? "" : "hidden"} onclick="flipSetting('${key}', this)"></div>`;
    } else if ((item.widget === "option" || item.widget === "multiple_button") && (item.choices || item.options)) {
      const opts = item.choices || item.options;
      control = `<select ${enabled ? "" : "disabled"} onchange="putSetting('${key}', this.value)">` +
        opts.map(o => `<option value="${o.value ?? o}" ${String(value) === String(o.value ?? o) ? "selected" : ""}>${o.label ?? o}</option>`).join("") + `</select>`;
    } else if (item.widget === "option" && item.min != null && item.max != null) {
      // 数值选项：min/max/step/unit（相机偏移、软件延迟等）
      const step = item.step || 1;
      const unit = (item.unit && typeof item.unit === "object") ? (paramsAll["IsMetric"] === "1" ? item.unit.metric : item.unit.imperial) : (item.unit || "");
      control = `<input type="number" min="${item.min}" max="${item.max}" step="${step}" value="${value ?? ""}" ${enabled ? "" : "disabled"} onchange="putSetting('${key}', this.value)" class="num-input">` +
        (unit ? `<span class="badge">${unit}</span>` : "");
    } else if (item.widget === "info") {
      control = `<span class="badge">${value ?? ""}</span>`;
    } else { // button / 未知控件：v1 灰显占位
      control = `<span class="badge">车机端设置</span>`;
    }
    out.push(`<div class="row ${enabled ? "" : "disabled"}${isSub ? " sub-item" : ""}" style="cursor:default">
        <div style="flex:1"><div class="k">${item.title || key}</div></div>${control}</div>`);
    // 条目级子选项（sub_items），如转向灯暂停的车速阈值/延迟
    (item.sub_items || []).forEach(sub => emit(sub, true));
  };
  (items || []).forEach(item => emit(item, false));
  return out.join("");
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
    if (item && item.needs_onroad_cycle) toast("已保存，重启 openpilot 后生效");
    else toast("设置成功");
  } catch (e) { toast(e.message, true); }
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2000);
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

async function boot() {
  if (token) {
    try { await api("/api/params"); return enter(); } catch (_) { if (token) showAuth(); return; }
  }
  showAuth();
}
boot();
