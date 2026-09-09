// boot：登录/首设不变；登录后进入 hash 路由。
import { api, getToken, setToken, setUnauthorizedHandler } from "./api.js";
import { route, rerender } from "./router.js";
import { store, refreshParams } from "./store.js";
import { bindControls } from "./components.js";
import { putSetting, toggleSetting } from "./views/panel.js";

const $ = (id) => document.getElementById(id);
let statusTimer = null;

async function pollStatus() {
  try {
    const s = await api("/api/status");
    store.status = s;
    $("conn").textContent = `已连接 · ${s.system.version} @ ${s.system.branch} ${s.system.commit || ""}`;
    if (s.paramsVersion !== undefined && s.paramsVersion !== store.paramsVersion) {
      const first = !store.primed;
      store.paramsVersion = s.paramsVersion;
      store.primed = true;
      if (!first && store.loaded) {
        await refreshParams();
        // 仅首页跟随重渲染；面板/模型页有自己的数据流
        if ((location.hash || "#/") === "#/") rerender();
      }
    }
  } catch (e) { $("conn").textContent = "连接失败"; }
}

function showAuth() {
  clearInterval(statusTimer);
  $("app").hidden = true;
  $("view-auth").hidden = false;
  $("card-login").hidden = false;
  fetch("/api/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
    .then(r => { $("card-setup").hidden = r.status === 409; })
    .catch(() => { $("card-setup").hidden = true; });
}

async function enter() {
  $("view-auth").hidden = true;
  $("app").hidden = false;
  await route($("app"));
  clearInterval(statusTimer);
  statusTimer = setInterval(pollStatus, 5000);
  pollStatus();
}

async function boot() {
  setUnauthorizedHandler(showAuth);
  bindControls($("app"), toggleSetting, putSetting);
  window.addEventListener("hashchange", () => route($("app")));
  if (getToken()) {
    try { await api("/api/params"); return enter(); } catch (_) { if (getToken()) showAuth(); return; }
  }
  showAuth();
}

$("login-btn").onclick = async () => {
  try {
    const { token: t } = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("login-pw").value }) });
    setToken(t);
    await enter();
  } catch (e) { $("auth-msg").textContent = e.message; }
};
$("setup-btn").onclick = async () => {
  if ($("setup-pw1").value !== $("setup-pw2").value || $("setup-pw1").value.length < 6) { $("auth-msg").textContent = "两次密码不一致或长度不足"; return; }
  try {
    await api("/api/setup", { method: "POST", body: JSON.stringify({ password: $("setup-pw1").value }) });
    const { token: t } = await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("setup-pw1").value }) });
    setToken(t);
    await enter();
  } catch (e) { $("auth-msg").textContent = e.message; }
};

boot();
