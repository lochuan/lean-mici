// 通用小组件：转义 / toast / 控件事件委托。零依赖。
export function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

let toastTimer = null;
export function toast(msg, isErr = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.toggle("err", isErr);
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2000);
}

// 控件事件委托：toggle 用 data-key 点击；select/number 用 data-key + data-act="put" change。
// 写操作回调由视图注入（app.js 装配，避免模块循环依赖）。
export function bindControls(root, onToggle, onPut) {
  root.addEventListener("click", (e) => {
    const sw = e.target.closest(".switch[data-key]");
    if (sw && !sw.classList.contains("disabled")) onToggle(sw.dataset.key, sw);
  });
  root.addEventListener("change", (e) => {
    const el = e.target.closest("[data-key][data-act]");
    if (el && !el.disabled) onPut(el.dataset.key, el.value);
  });
}
