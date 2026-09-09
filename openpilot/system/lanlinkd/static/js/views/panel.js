// 面板页：单面板渲染（sections/sub_panels/vehicle_settings），禁用灰显 + 原因。
import { api } from "../api.js";
import { store, ensureData, findItem } from "../store.js";
import { evalRules } from "../rules.js";
import { esc, toast } from "../components.js";

export async function renderPanel(app, id) {
  await ensureData();
  const panel = ((store.settings || {}).panels || []).find(p => p.id === id);
  app.innerHTML = "";
  const wrap = document.createElement("div");
  if (!panel && id !== "vehicle") {
    wrap.innerHTML = `<div class="empty-state">面板不存在</div>`;
    app.appendChild(wrap);
    return;
  }
  const head = document.createElement("div");
  head.className = "panel-head";
  head.innerHTML = `<a class="back" href="#/">← 返回</a><h2>${esc(panel ? (panel.label || panel.id) : "车辆")}</h2>`;
  app.appendChild(head);

  const body = document.createElement("div");
  body.id = "panel-body";
  if (panel) {
    body.innerHTML = (panel.sections || []).map(sec => renderSection(sec)).join("") +
      (panel.sub_panels || []).map(sp => renderSubPanel(sp, true)).join("");
  } else {
    // 车辆面板：按 brand 注入 vehicle_settings（无匹配品牌 → 空态）
    const brand = (store.caps || {}).brand;
    const vehicle = ((store.settings || {}).vehicle_settings || {})[brand];
    body.innerHTML = vehicle ? renderSection(vehicle) : `<div class="empty-state">未识别到车辆品牌</div>`;
  }
  app.appendChild(body);
}

function renderSection(sec) {
  if (sec.visibility && !evalRules(sec.visibility).ok) return "";
  const secEnabled = evalRules(sec.enablement).ok;
  const rows = renderItems(sec.items, secEnabled);
  const subs = (sec.sub_panels || []).map(sp => renderSubPanel(sp, secEnabled)).join("");
  return (sec.title ? `<h3 class="sep">${esc(sec.title)}</h3>` : "") +
    (sec.description ? `<div class="desc">${esc(sec.description)}</div>` : "") + rows + subs;
}

function renderItems(items, secEnabled) {
  const out = [];
  const emit = (item, isSub) => {
    const key = item.key, m = store.meta[key] || {};
    if (!evalRules(item.visibility).ok) return; // visibility = 不适用，不渲染
    if (m.blocked || item.blocked) return;
    const missing = item._missing || m._missing;
    const value = store.paramsAll[key];
    const ev = evalRules(item.enablement);
    const enabled = secEnabled && ev.ok && !missing;
    const reason = enabled ? "" : (ev.reasons[0] || (missing ? "车机端缺少该参数" : ""));
    out.push(rowHTML(item, key, value, enabled, reason, isSub));
    (item.sub_items || []).forEach(sub => emit(sub, true));
  };
  (items || []).forEach(item => emit(item, false));
  return out.join("");
}

function rowHTML(item, key, value, enabled, reason, isSub) {
  let control = "";
  const dis = enabled ? "" : "disabled";
  if (item.widget === "toggle") {
    // 灰显但可见：禁用时点击被 .disabled 拦截，不再 hidden
    control = `<div class="ctl"><div class="switch ${String(value) === "1" ? "on" : ""} ${enabled ? "" : "disabled"}" data-key="${esc(key)}"></div></div>`;
  } else if ((item.widget === "option" || item.widget === "multiple_button") && (item.choices || item.options)) {
    const opts = item.choices || item.options;
    control = `<div class="ctl"><select data-key="${esc(key)}" data-act="put" ${dis}>` +
      opts.map(o => `<option value="${esc(o.value ?? o)}" ${String(value) === String(o.value ?? o) ? "selected" : ""}>${esc(o.label ?? o)}</option>`).join("") +
      `</select></div>`;
  } else if (item.widget === "option" && item.min != null && item.max != null) {
    // 数值选项：min/max/step/unit（相机偏移、软件延迟等）
    const step = item.step || 1;
    const unit = (item.unit && typeof item.unit === "object")
      ? (store.paramsAll["IsMetric"] === "1" ? item.unit.metric : item.unit.imperial) : (item.unit || "");
    control = `<div class="ctl"><input class="num-input" type="number" data-key="${esc(key)}" data-act="put" min="${esc(item.min)}" max="${esc(item.max)}" step="${esc(step)}" value="${esc(value ?? "")}" ${dis}>` +
      (unit ? `<span class="badge">${esc(unit)}</span>` : "") + `</div>`;
  } else if (item.widget === "info") {
    control = `<div class="ctl"><span class="badge">${esc(value ?? "")}</span></div>`;
  } else {
    // button / 未知控件：v1 灰显占位
    control = `<div class="ctl"><span class="badge">车机端设置</span></div>`;
  }
  return `<div class="row ${enabled ? "" : "disabled"}${isSub ? " sub-item" : ""}" data-rowkey="${esc(key)}" style="cursor:default">
      <div style="flex:1"><div class="k">${esc(item.title || key)}</div>
        ${item.description ? `<div class="reason">${esc(item.description)}</div>` : ""}
        ${reason ? `<div class="reason">· ${esc(reason)}</div>` : ""}</div>${control}</div>`;
}

function renderSubPanel(sp, secEnabled) {
  // 上游 schema：SubPanel 由 trigger_condition 触发才显示；无条件恒显
  if (sp.trigger_condition && !evalRules([sp.trigger_condition]).ok) return "";
  return `<div class="sub-panel"><div class="sub-label">${esc(sp.label || sp.id)}</div>` +
    renderItems(sp.items, secEnabled) + `</div>`;
}

export async function toggleSetting(key) {
  const cur = String(store.paramsAll[key]) === "1";
  await putSetting(key, cur ? "0" : "1");
}

export async function putSetting(key, value) {
  try {
    await api(`/api/params/${key}`, { method: "PUT", body: JSON.stringify({ value: String(value) }) });
    store.paramsAll[key] = String(value);
    const item = findItem(key);
    if (item && item.needs_onroad_cycle) toast("已保存，重启 openpilot 后生效");
    else toast("设置成功");
    // 重渲染当前面板以反映依赖联动（如 ShowAdvancedControls 开关其它条目）
    const h = location.hash || "";
    if (h.startsWith("#/panel/")) await renderPanel(document.getElementById("app"), h.slice("#/panel/".length));
  } catch (e) { toast(e.message, true); }
}
