// fetch 封装 + token 管理。零依赖。
let token = localStorage.getItem("lanlink_token") || "";
let onUnauthorized = null;

export function getToken() { return token; }
export function setToken(t) {
  token = t || "";
  if (t) localStorage.setItem("lanlink_token", t);
  else localStorage.removeItem("lanlink_token");
}
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (res.status === 401 && path !== "/api/login" && path !== "/api/setup") {
    if (onUnauthorized) onUnauthorized();
    throw new Error("unauthorized");
  }
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}
