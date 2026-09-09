# system/lanlinkd/lanlinkd.py
"""LANLink daemon：局域网版 sunnylink（无云）。aiohttp 装配层。"""
import json
import os
import threading

from aiohttp import web

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.common.hardware import HARDWARE, PC
from openpilot.common.hardware.hw import Paths
from openpilot.system.lanlinkd import logs as logs_mod
from openpilot.system.lanlinkd import params_api
from openpilot.system.lanlinkd import settings as settings_mod
from openpilot.system.lanlinkd.auth import (
  MIN_PASSWORD_LEN, LoginThrottle, SessionStore, hash_password, verify_password)
from openpilot.system.lanlinkd.statusd import StatusCache

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
VERSION_PARAMS = ("Version", "GitBranch", "GitCommit")

routes = web.RouteTableDef()


def _json_error(status: int, message: str) -> web.Response:
  return web.json_response({"error": message}, status=status)


class LanlinkApp:
  def __init__(self):
    self.params = Params()
    self.sessions = SessionStore()
    self.throttle = LoginThrottle()
    self.version_info = {k: params_api.to_str(self.params.get(k)) or "" for k in VERSION_PARAMS}
    self.cache = StatusCache(self.version_info, device_type="pc" if PC else HARDWARE.get_device_type(), params=self.params)
    self.exit_event = threading.Event()
    self._settings_ui: dict | None = None
    threading.Thread(target=self.cache.run, args=(self.exit_event,), name="lanlink_status", daemon=True).start()

  # ---- helpers ----
  def _authorized(self, request: web.Request) -> bool:
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and self.sessions.validate(auth.removeprefix("Bearer "))

  def _key_exists(self, key: str) -> bool:
    try:
      self.params.check_key(key)
      return True
    except Exception:
      return False

  def _settings(self) -> dict:
    if self._settings_ui is None:
      with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_ui.json")) as f:
        self._settings_ui = settings_mod.mark_missing_keys(json.load(f), self._key_exists)
    return self._settings_ui

  # ---- auth endpoints ----
  @routes.post("/api/setup")
  async def setup(self, request: web.Request) -> web.Response:
    if self.params.get("LanLinkPasswordHash"):
      return _json_error(409, "password already set")
    body = await request.json()
    password = str(body.get("password", ""))
    if len(password) < MIN_PASSWORD_LEN:
      return _json_error(400, f"password must be >= {MIN_PASSWORD_LEN} chars")
    self.params.put("LanLinkPasswordHash", hash_password(password), block=True)
    return web.Response(status=204)

  @routes.post("/api/login")
  async def login(self, request: web.Request) -> web.Response:
    stored = self.params.get("LanLinkPasswordHash")
    if not stored:
      return _json_error(409, "password not set")
    locked, remaining = self.throttle.is_locked()
    if locked:
      return _json_error(429, f"locked, retry in {remaining}s")
    body = await request.json()
    if not verify_password(str(body.get("password", "")), stored):
      self.throttle.record_failure()
      return _json_error(401, "wrong password")
    self.throttle.reset()
    return web.json_response({"token": self.sessions.issue()})

  @routes.post("/api/password")
  async def change_password(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    body = await request.json()
    stored = self.params.get("LanLinkPasswordHash")
    if not verify_password(str(body.get("old", "")), stored or ""):
      return _json_error(401, "wrong old password")
    new = str(body.get("new", ""))
    if len(new) < MIN_PASSWORD_LEN:
      return _json_error(400, f"password must be >= {MIN_PASSWORD_LEN} chars")
    self.params.put("LanLinkPasswordHash", hash_password(new), block=True)
    self.sessions.revoke_all()  # 全端下线，需重新登录
    return web.Response(status=204)

  # ---- params endpoints ----
  @routes.get("/api/params")
  async def params_list(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return web.json_response(params_api.list_params(self.params))

  @routes.get("/api/params/_all")
  async def params_all(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return web.json_response(params_api.read_all(self.params))

  @routes.get("/api/params/{key}")
  async def params_get(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, value = params_api.read_param(self.params, request.match_info["key"])
    return (web.json_response({"value": value}) if code == 200 else _json_error(code, "denied"))

  @routes.put("/api/params/{key}")
  async def params_put(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    body = await request.json()
    code, message = params_api.write_param(self.params, request.match_info["key"], str(body.get("value", "")))
    return (web.Response(status=204) if code == 204 else _json_error(code, message))

  @routes.delete("/api/params/{key}")
  async def params_delete(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, _ = params_api.delete_param(self.params, request.match_info["key"])
    return (web.Response(status=204) if code == 204 else _json_error(code, "denied"))

  # ---- status / capabilities / settings / logs ----
  @routes.get("/api/status")
  async def status(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    snap = self.cache.snapshot()
    snap["paramsVersion"] = params_api.to_str(self.params.get(params_api.VERSION_KEY))
    return web.json_response(snap)

  @routes.get("/api/capabilities")
  async def capabilities(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return web.json_response(self.cache.capabilities())

  @routes.get("/api/settings_ui")
  async def settings_ui(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return web.json_response(self._settings())

  @routes.get("/api/logs")
  async def logs_list(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return web.json_response(logs_mod.list_routes(Paths.log_root()))

  @routes.get("/api/logs/{route}/{fname:.+}")
  async def logs_file(self, request: web.Request) -> web.Response:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    path = logs_mod.resolve_log_file(Paths.log_root(), request.match_info["route"], request.match_info["fname"])
    if path is None:
      return _json_error(404, "not found")
    return web.FileResponse(path)
  # ---- static ----
  @routes.get("/")
  async def index(self, request: web.Request) -> web.Response:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))

  @routes.get("/static/{filename}")
  async def static_file(self, request: web.Request) -> web.Response:
    # no-cache：OTA 换版后浏览器不能靠启发式缓存拿到旧 JS/HTML
    safe = os.path.normpath(request.match_info["filename"])
    path = os.path.join(STATIC_DIR, safe)
    if os.path.commonpath([STATIC_DIR, path]) == STATIC_DIR and os.path.isfile(path):
      resp = web.FileResponse(path)
      resp.headers["Cache-Control"] = "no-cache"
      return resp
    raise web.HTTPNotFound()


def create_app() -> web.Application:
  app = web.Application(client_max_size=2 * 1024 * 1024)
  state = LanlinkApp()

  @web.middleware
  async def auth_middleware(request: web.Request, handler):
    public = request.path in ("/", "/api/login", "/api/setup") or request.path.startswith("/static/")
    if not public and not state._authorized(request):
      return _json_error(401, "unauthorized")
    return await handler(request)

  # 绑定实例方法后逐条注册（RouteTableDef 存的是未绑定函数）
  for route in routes:
    app.router.add_route(route.method, route.path, getattr(state, route.handler.__name__))
  app.middlewares.append(auth_middleware)
  return app


def main() -> None:
  cloudlog.info("lanlinkd starting on 0.0.0.0:8088")
  web.run_app(create_app(), host="0.0.0.0", port=8088, print=None)


if __name__ == "__main__":
  main()
