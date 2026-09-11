# system/lanlinkd/lanlinkd.py
"""LANLink daemon：局域网版 sunnylink（无云）。Sanic 装配层。

为什么是 Sanic 而不是 aiohttp：aiohttp 曾由 AGNOS venv 提供，19.6 起被移除，
于是 lanlinkd 在设备上直接 ModuleNotFoundError（CI 仍绿，因为 CI 的 venv 有）。
现在 Web 框架由 tools/install_device_pydeps.sh 钉版安装进 /data/pydeps，
不再依赖 AGNOS 碰巧带了什么。

为什么 single_process=True（见 main()）：Sanic 默认起多 worker 进程，而
SessionStore / LoginThrottle 是**进程内内存状态**。多 worker 下同一 token 只在签发它
的那个进程有效，登录会随机失效，防爆破计数也会被稀释成 worker 份数倍。
"""
import json
import os
import threading

from sanic import Sanic
from sanic.request import Request
from sanic.response import HTTPResponse, empty, file, json as json_response

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.common.hardware import HARDWARE, PC
from openpilot.common.hardware.hw import Paths
from openpilot.system.lanlinkd import logs as logs_mod
from openpilot.system.lanlinkd import models_api
from openpilot.system.lanlinkd import params_api
from openpilot.system.lanlinkd import settings as settings_mod
from openpilot.system.lanlinkd import vehicle_api
from openpilot.system.lanlinkd.auth import (
  MIN_PASSWORD_LEN, LoginThrottle, SessionStore, hash_password, verify_password)
from openpilot.system.lanlinkd.statusd import StatusCache

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
VERSION_PARAMS = ("Version", "GitBranch", "GitCommit")
MAX_BODY_BYTES = 2 * 1024 * 1024

# 无需 token 的路径。auth 中间件是唯一的准入判断点，所以这张表就是完整的
# 公开面——审计时只看这里，不必翻每个 handler。
PUBLIC_PATHS = ("/", "/api/login", "/api/setup")
PUBLIC_PREFIXES = ("/static/", "/assets/")


def _json_error(status: int, message: str) -> HTTPResponse:
  return json_response({"error": message}, status=status)


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
  def _authorized(self, request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    return auth.startswith("Bearer ") and self.sessions.validate(auth.removeprefix("Bearer "))

  @staticmethod
  def _body(request: Request) -> dict:
    # Sanic 的 request.json 对非法 JSON 抛 BadRequest(400)；此处统一成空 dict，
    # 让各 handler 自己按缺字段返回 400，错误信息比框架默认的更具体。
    try:
      body = request.json
    except Exception:
      return {}
    return body if isinstance(body, dict) else {}

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
  async def setup(self, request: Request) -> HTTPResponse:
    if self.params.get("LanLinkPasswordHash"):
      return _json_error(409, "password already set")
    password = str(self._body(request).get("password", ""))
    if len(password) < MIN_PASSWORD_LEN:
      return _json_error(400, f"password must be >= {MIN_PASSWORD_LEN} chars")
    self.params.put("LanLinkPasswordHash", hash_password(password), block=True)
    return empty(status=204)

  async def login(self, request: Request) -> HTTPResponse:
    stored = self.params.get("LanLinkPasswordHash")
    if not stored:
      return _json_error(409, "password not set")
    locked, remaining = self.throttle.is_locked()
    if locked:
      return _json_error(429, f"locked, retry in {remaining}s")
    if not verify_password(str(self._body(request).get("password", "")), stored):
      self.throttle.record_failure()
      return _json_error(401, "wrong password")
    self.throttle.reset()
    return json_response({"token": self.sessions.issue()})

  async def change_password(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    body = self._body(request)
    stored = self.params.get("LanLinkPasswordHash")
    if not verify_password(str(body.get("old", "")), stored or ""):
      return _json_error(401, "wrong old password")
    new = str(body.get("new", ""))
    if len(new) < MIN_PASSWORD_LEN:
      return _json_error(400, f"password must be >= {MIN_PASSWORD_LEN} chars")
    self.params.put("LanLinkPasswordHash", hash_password(new), block=True)
    self.sessions.revoke_all()  # 全端下线，需重新登录
    return empty(status=204)

  # ---- params endpoints ----
  async def params_list(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(params_api.list_params(self.params))

  async def params_all(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(params_api.read_all(self.params))

  async def params_get(self, request: Request, key: str) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, value = params_api.read_param(self.params, key)
    return (json_response({"value": value}) if code == 200 else _json_error(code, "denied"))

  async def params_put(self, request: Request, key: str) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, message = params_api.write_param(self.params, key, str(self._body(request).get("value", "")))
    return (empty(status=204) if code == 204 else _json_error(code, message))

  async def params_delete(self, request: Request, key: str) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, _ = params_api.delete_param(self.params, key)
    return (empty(status=204) if code == 204 else _json_error(code, "denied"))

  # ---- models ----
  async def models_get(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(models_api.models_state(self.params, self.cache.download(), Paths.model_root()))

  async def models_select(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, msg = models_api.select(self.params, str(self._body(request).get("ref", "")))
    return (empty(status=204) if code == 204 else _json_error(code, msg))

  async def models_cancel(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, msg = models_api.cancel(self.params)
    return (empty(status=204) if code == 204 else _json_error(code, msg))

  async def models_refresh(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, msg = models_api.refresh(self.params)
    return (empty(status=204) if code == 204 else _json_error(code, msg))

  async def models_clear_cache(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    code, msg = models_api.clear_cache(self.params)
    return (empty(status=204) if code == 204 else _json_error(code, msg))

  async def models_fav(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    body = self._body(request)
    code, msg = models_api.set_fav(self.params, str(body.get("ref", "")), bool(body.get("on")))
    return (empty(status=204) if code == 204 else _json_error(code, msg))

  # ---- vehicle（指纹 / 平台选择）----
  async def vehicle_get(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(vehicle_api.vehicle_state(self.params))

  async def vehicle_select(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    name = str(self._body(request).get("name", ""))
    code, msg = vehicle_api.select_platform(self.params, name)
    return (empty(status=204) if code == 204 else _json_error(code, msg))

  # ---- status / capabilities / settings / logs ----
  async def status(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    snap = self.cache.snapshot()
    snap["paramsVersion"] = params_api.to_str(self.params.get(params_api.VERSION_KEY))
    return json_response(snap)

  async def capabilities(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(self.cache.capabilities())

  async def settings_ui(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(self._settings())

  async def logs_list(self, request: Request) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    return json_response(logs_mod.list_routes(Paths.log_root()))

  async def logs_file(self, request: Request, route: str, fname: str) -> HTTPResponse:
    if not self._authorized(request):
      return _json_error(401, "unauthorized")
    path = logs_mod.resolve_log_file(Paths.log_root(), route, fname)
    if path is None:
      return _json_error(404, "not found")
    return await file(path)

  # ---- static ----
  async def index(self, request: Request) -> HTTPResponse:
    return await self._serve_static("index.html")

  async def static_file(self, request: Request, path: str) -> HTTPResponse:
    return await self._serve_static(path)

  async def asset_file(self, request: Request, path: str) -> HTTPResponse:
    # Vite 产出 /assets/*，文件名自带内容 hash，可长缓存
    return await self._serve_static(os.path.join("assets", path), immutable=True)

  async def _serve_static(self, rel: str, immutable: bool = False) -> HTTPResponse:
    # 路径穿越防护：normpath 后必须仍在 STATIC_DIR 内
    resolved = os.path.normpath(os.path.join(STATIC_DIR, rel))
    if os.path.commonpath([STATIC_DIR, resolved]) != STATIC_DIR or not os.path.isfile(resolved):
      return _json_error(404, "not found")
    # hash 命名的 assets 可长缓存；index.html / 无 hash 文件必须 no-cache，
    # 否则 OTA 换版后浏览器靠启发式缓存拿到旧 UI 却配新 API。
    # header key 必须小写：sanic.response.file() 内部用 headers.setdefault("cache-control", ...)，
    # 大写 key 不会命中它的 setdefault，两个值都会发出去（Cache-Control: no-cache, no-cache）。
    cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
    return await file(resolved, headers={"cache-control": cache})


# 唯一的路由表。放在一处便于审计"哪些路径存在、哪些是公开的"。
ROUTES: tuple[tuple[str, str, str], ...] = (
  ("POST", "/api/setup", "setup"),
  ("POST", "/api/login", "login"),
  ("POST", "/api/password", "change_password"),
  ("GET", "/api/params", "params_list"),
  ("GET", "/api/params/_all", "params_all"),
  ("GET", "/api/params/<key:str>", "params_get"),
  ("PUT", "/api/params/<key:str>", "params_put"),
  ("DELETE", "/api/params/<key:str>", "params_delete"),
  ("GET", "/api/models", "models_get"),
  ("POST", "/api/models/select", "models_select"),
  ("POST", "/api/models/cancel", "models_cancel"),
  ("POST", "/api/models/refresh", "models_refresh"),
  ("POST", "/api/models/clear_cache", "models_clear_cache"),
  ("POST", "/api/models/fav", "models_fav"),
  ("GET", "/api/vehicle", "vehicle_get"),
  ("POST", "/api/vehicle/select", "vehicle_select"),
  ("GET", "/api/status", "status"),
  ("GET", "/api/capabilities", "capabilities"),
  ("GET", "/api/settings_ui", "settings_ui"),
  ("GET", "/api/logs", "logs_list"),
  ("GET", "/api/logs/<route:str>/<fname:path>", "logs_file"),
  ("GET", "/", "index"),
  ("GET", "/static/<path:path>", "static_file"),
  ("GET", "/assets/<path:path>", "asset_file"),
)


def is_public(path: str) -> bool:
  return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def create_app(name: str = "lanlinkd") -> Sanic:
  app = Sanic(name)
  app.config.REQUEST_MAX_SIZE = MAX_BODY_BYTES
  app.config.ACCESS_LOG = False
  # 设备是局域网内单用户访问，keepalive 超时无需长挂
  app.config.KEEP_ALIVE_TIMEOUT = 15

  state = LanlinkApp()
  app.ctx.state = state

  for method, path, handler_name in ROUTES:
    app.add_route(getattr(state, handler_name), path, methods=[method], name=handler_name)

  @app.on_request
  async def auth_middleware(request: Request):
    if not is_public(request.path) and not state._authorized(request):
      return _json_error(401, "unauthorized")
    return None

  return app


def main() -> None:
  # 默认关闭：仅当 UI（LanLinkEnabled）开启时提供服务。manager 已按 param 门控，
  # 这里再自保护一层，防其它启动链路误拉起（UI 关闭时立刻退出）
  if not Params().get_bool("LanLinkEnabled"):
    cloudlog.info("lanlinkd: LanLinkEnabled off, exiting")
    return
  cloudlog.info("lanlinkd starting on 0.0.0.0:8088")
  # single_process=True 是必须的，不是调优：session/throttle 是进程内状态，
  # 多 worker 会让登录随机失效、防爆破计数被稀释。详见模块 docstring。
  create_app().run(host="0.0.0.0", port=8088, single_process=True, motd=False)


if __name__ == "__main__":
  main()
