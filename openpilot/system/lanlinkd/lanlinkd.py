# system/lanlinkd/lanlinkd.py
"""LANLink daemon：局域网版 sunnylink（无云）。Sanic 装配层。

为什么是 Sanic 而不是 aiohttp：aiohttp 曾由 AGNOS venv 提供，19.6 起被移除，
于是 lanlinkd 在设备上直接 ModuleNotFoundError（CI 仍绿，因为 CI 的 venv 有）。
Sanic 由 pyproject.toml 声明，随 AGNOS venv 一起安装（19.7.2 起），不再需要
/data/pydeps 旁路。

为什么 single_process=True（见 main()）：Sanic 默认起多 worker 进程，而
StatusCache 状态线程、WifiManager DBus 单例都是**进程内状态**。
多 worker 下各自起一套线程订阅 msgq/NM，重复开销且互相打架。
"""

import asyncio
import json
import os
import socket
import threading

from sanic import Sanic
from sanic.request import Request
from sanic.response import HTTPResponse, empty, file, json as json_response

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.common.hardware import HARDWARE, PC
from openpilot.common.hardware.hw import Paths
from openpilot.sunnypilot.system.bluetooth import BluetoothClient
from openpilot.system.lanlinkd import bluetooth_api
from openpilot.system.lanlinkd import logs as logs_mod
from openpilot.system.lanlinkd import mdns
from openpilot.system.lanlinkd import params_api
from openpilot.system.lanlinkd import settings as settings_mod
from openpilot.system.lanlinkd import vehicle_api
from openpilot.system.lanlinkd import wifi_api
from openpilot.system.lanlinkd import software_api
from openpilot.system.lanlinkd.statusd import StatusCache

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
VERSION_PARAMS = ("Version", "GitBranch", "GitCommit")
MAX_BODY_BYTES = 2 * 1024 * 1024


def _json_error(status: int, message: str) -> HTTPResponse:
  return json_response({"error": message}, status=status)


class LanlinkApp:
  def __init__(self):
    self.params = Params()
    self.version_info = {k: params_api.to_str(self.params.get(k)) or "" for k in VERSION_PARAMS}
    self.cache = StatusCache(self.version_info, device_type="pc" if PC else HARDWARE.get_device_type(), params=self.params)
    self.exit_event = threading.Event()
    self._settings_ui: dict | None = None
    self._wifi_manager = None
    self._wifi_lock = threading.Lock()
    threading.Thread(target=self.cache.run, args=(self.exit_event,), name="lanlink_status", daemon=True).start()

  # ---- helpers ----
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

  # ---- params endpoints ----
  async def params_list(self, request: Request) -> HTTPResponse:
    return json_response(params_api.list_params(self.params))

  async def params_all(self, request: Request) -> HTTPResponse:
    return json_response(params_api.read_all(self.params))

  async def params_get(self, request: Request, key: str) -> HTTPResponse:
    code, value = params_api.read_param(self.params, key)
    return json_response({"value": value}) if code == 200 else _json_error(code, "denied")

  async def params_put(self, request: Request, key: str) -> HTTPResponse:
    code, message = params_api.write_param(self.params, key, str(self._body(request).get("value", "")))
    return empty(status=204) if code == 204 else _json_error(code, message)

  async def params_delete(self, request: Request, key: str) -> HTTPResponse:
    code, _ = params_api.delete_param(self.params, key)
    return empty(status=204) if code == 204 else _json_error(code, "denied")

  # ---- vehicle（指纹 / 平台选择）----
  async def vehicle_get(self, request: Request) -> HTTPResponse:
    return json_response(vehicle_api.vehicle_state(self.params))

  async def vehicle_select(self, request: Request) -> HTTPResponse:
    name = str(self._body(request).get("name", ""))
    code, msg = vehicle_api.select_platform(self.params, name)
    return empty(status=204) if code == 204 else _json_error(code, msg)

  # ---- bluetooth ----
  # BluetoothClient 是阻塞 socket IO（status 10s、set_power 引导最长 90s），
  # 必须丢进线程池：single_process 下阻塞事件循环会让整个 Web UI 冻住。
  async def bluetooth_get(self, request: Request) -> HTTPResponse:
    code, body = await asyncio.to_thread(
      bluetooth_api.status_payload, BluetoothClient(timeout=bluetooth_api.BLUETOOTH_TIMEOUT), self.params
    )
    return json_response(body, status=code)

  async def bluetooth_operation(self, request: Request, operation: str) -> HTTPResponse:
    code, body = await asyncio.to_thread(
      bluetooth_api.run_operation,
      BluetoothClient(timeout=bluetooth_api.BLUETOOTH_TIMEOUT),
      self.params,
      operation,
      self._body(request),
    )
    return json_response(body, status=code)

  # ---- wifi ----
  # WifiManager(nm DBus) 是阻塞 IO，与 bluetooth 同理走线程池。
  # 单例懒加载：DBUS 不可用时退化为 fallback 快照（页面可渲染）。
  def _get_wifi(self):
    with self._wifi_lock:
      if self._wifi_manager is None:
        from openpilot.system.ui.lib.wifi_manager import WifiManager
        self._wifi_manager = WifiManager(manage_tethering=False)
      return self._wifi_manager

  def _wifi_body(self) -> dict:
    # 读缓存快照（wifi_api.snapshot），/api/wifi 与 /api/connectivity 共用这条路径
    try:
      return wifi_api.snapshot(self._get_wifi(), self.params.get_bool("IsOffroad"))
    except Exception as exc:
      return wifi_api.fallback_snapshot(exc)

  async def wifi_get(self, request: Request) -> HTTPResponse:
    return json_response(await asyncio.to_thread(self._wifi_body))

  async def wifi_operation(self, request: Request, operation: str) -> HTTPResponse:
    if operation not in wifi_api.OPERATIONS:
      return _json_error(404, "Unknown WiFi operation.")
    if operation in wifi_api.OFFROAD_ONLY and not self.params.get_bool("IsOffroad"):
      return _json_error(409, "WiFi settings can only be changed offroad.")
    req_body = self._body(request)

    def worker():
      mgr = self._get_wifi()
      if operation == "connect":
        code, msg, payload = wifi_api.validate_connect_body(req_body)
        if code:
          return code, {"error": msg}
        # 先落静态 IP 再连接：NM AddAndActivateConnection2 是 volatile profile，
        # 连接成功落盘后这些字段随 profile 一起持久化。
        mgr.connect_to_network(payload["ssid"], payload["password"], hidden=payload["hidden"])
        result = {}
      else:
        ssid = str(req_body.get("ssid", "")).strip()
        if not ssid:
          return 400, {"error": "SSID is required."}
        if operation == "forget":
          mgr.forget_connection(ssid, block=True)
          return 200, {}
        if operation == "activate":
          mgr.activate_connection(ssid, block=True)
          return 200, {}
        try:
          cfg = wifi_api.validate_static_config(req_body)
        except wifi_api.WifiValidationError as exc:
          return 400, {"error": str(exc)}
        result = mgr.set_static_ip(
          ssid, cfg["ip"], cfg["prefix"], cfg["gateway"], cfg["dns"], block=True
        ) or {}
        return 200 if not result.get("error") else 502, result or {}

      return 200, result

    code, body = await asyncio.to_thread(worker)
    return json_response(body, status=code)

  # ---- connectivity（Wi-Fi + 蓝牙合并快照）----
  async def connectivity(self, request: Request) -> HTTPResponse:
    # 蓝牙 daemon 出错不拖垮整个接口：bluetooth 部分照 /api/bluetooth 的降级
    # 快照带上 error，整体照样 200——连接页一方出错另一方照常显示。
    wifi = await asyncio.to_thread(self._wifi_body)
    _, bluetooth = await asyncio.to_thread(
      bluetooth_api.status_payload, BluetoothClient(timeout=bluetooth_api.BLUETOOTH_TIMEOUT), self.params
    )
    return json_response({"wifi": wifi, "bluetooth": bluetooth})

  # ---- software (updater) ----
  async def software_get(self, request: Request) -> HTTPResponse:
    return json_response(software_api.status(self.params))

  async def software_action(self, request: Request, action: str) -> HTTPResponse:
    code, payload = software_api.signal(self.params, action)
    if code == 200:
      return empty(status=204)
    if isinstance(payload, str):
      return _json_error(code, payload)
    return json_response(payload, status=code)

  # ---- status / capabilities / settings / logs ----
  async def status(self, request: Request) -> HTTPResponse:
    snap = self.cache.snapshot()
    snap["paramsVersion"] = params_api.to_str(self.params.get(params_api.VERSION_KEY))
    return json_response(snap)

  async def bootstrap(self, request: Request) -> HTTPResponse:
    # 进「车机」页一次拿全三份数据：各字段与原接口同形（App 一次请求渲染首屏）
    return json_response({
      "settings_ui": self._settings(),
      "params": params_api.read_all(self.params),
      "capabilities": self.cache.capabilities(),
      "paramsVersion": params_api.to_str(self.params.get(params_api.VERSION_KEY)),
    })

  async def capabilities(self, request: Request) -> HTTPResponse:
    return json_response(self.cache.capabilities())

  async def settings_ui(self, request: Request) -> HTTPResponse:
    return json_response(self._settings())

  async def logs_list(self, request: Request) -> HTTPResponse:
    return json_response(logs_mod.list_routes(Paths.log_root()))

  async def logs_file(self, request: Request, route: str, fname: str) -> HTTPResponse:
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


# 唯一的路由表。放在一处便于审计"哪些路径存在、暴露了什么"。
ROUTES: tuple[tuple[str, str, str], ...] = (
  ("GET", "/api/params", "params_list"),
  ("GET", "/api/params/_all", "params_all"),
  ("GET", "/api/params/<key:str>", "params_get"),
  ("PUT", "/api/params/<key:str>", "params_put"),
  ("DELETE", "/api/params/<key:str>", "params_delete"),
  ("GET", "/api/vehicle", "vehicle_get"),
  ("POST", "/api/vehicle/select", "vehicle_select"),
  ("GET", "/api/bluetooth", "bluetooth_get"),
  ("POST", "/api/bluetooth/<operation:str>", "bluetooth_operation"),
  ("GET", "/api/wifi", "wifi_get"),
  ("GET", "/api/connectivity", "connectivity"),
  ("POST", "/api/wifi/<operation:str>", "wifi_operation"),
  ("GET", "/api/software", "software_get"),
  ("POST", "/api/software/<action:str>", "software_action"),
  ("GET", "/api/status", "status"),
  ("GET", "/api/bootstrap", "bootstrap"),
  ("GET", "/api/capabilities", "capabilities"),
  ("GET", "/api/settings_ui", "settings_ui"),
  ("GET", "/api/logs", "logs_list"),
  ("GET", "/api/logs/<route:str>/<fname:path>", "logs_file"),
  ("GET", "/", "index"),
  ("GET", "/static/<path:path>", "static_file"),
  ("GET", "/assets/<path:path>", "asset_file"),
)


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

  return app


def main() -> None:
  # 默认关闭：仅当 UI（LanLinkEnabled）开启时提供服务。manager 已按 param 门控，
  # 这里再自保护一层，防其它启动链路误拉起（UI 关闭时立刻退出）
  params = Params()
  if not params.get_bool("LanLinkEnabled"):
    cloudlog.info("lanlinkd: LanLinkEnabled off, exiting")
    return
  cloudlog.info("lanlinkd starting on 0.0.0.0:8088")
  # mDNS 发布跟随本进程生死：退出（含 SIGINT）时 finally 结束子进程，广播消失。
  # 设备名 = hostname（AGNOS 上是 comma-<serial>）；TXT 带版本和设备名。
  device_name = socket.gethostname()
  mdns_proc = mdns.start(device_name, params_api.to_str(params.get("Version")) or "", device_name)
  try:
    # single_process=True 是必须的，不是调优：cache 状态线程 / WifiManager 单例
    # 是进程内状态，多 worker 会各起一套互相打架。详见模块 docstring。
    create_app().run(host="0.0.0.0", port=8088, single_process=True, motd=False)
  finally:
    mdns.stop(mdns_proc)


if __name__ == "__main__":
  main()
