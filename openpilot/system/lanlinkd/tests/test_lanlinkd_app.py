"""lanlinkd 装配层测试（Sanic）。

aiohttp 时代这一层零测试——框架迁移最容易改错的恰恰是路由匹配和错误语义，
而不是被测得很好的纯逻辑模块。这里用 Sanic 自带的 test_client 跑真实 HTTP，
覆盖：路径参数、错误语义、静态文件穿越防护。
"""
import os

import pytest

from openpilot.common.params import UnknownKeyName
from openpilot.system.lanlinkd import hud, lanlinkd as mod


class FakeParams:
  """duck-type Params：BOOL/STRING/INT 足够覆盖装配层。

  get_type 必须返回带 .name 的枚举式对象，不能返回裸字符串：真实 Params
  返回 ParamKeyType 枚举，params_api.type_name 先看 .name、否则 int(raw)，
  裸字符串会让它 ValueError。这个 fake 必须和真实契约一致，否则测试绿了
  但设备上 500。
  """

  _TYPES = {"LanLinkEnabled": "BOOL", "LanLinkParamsVersion": "INT", "TestToggle": "BOOL",
            # LanlinkApp.__init__ 读这三个组版本信息；设备上它们都存在
            "Version": "STRING", "GitBranch": "STRING", "GitCommit": "STRING",
            # bluetooth 降级快照从 params 推导；设备上它们都存在
            "BluetoothEnabled": "BOOL", "BluetoothAudioAddress": "STRING"}

  class _Type:
    def __init__(self, name: str):
      self.name = name

  def __init__(self, values=None):
    self._v = {"TestToggle": False, "LanLinkParamsVersion": 0}
    self._v.update(values or {})

  def get(self, key, *a, **k):
    # 真实 Params.get() 对未知 key 抛 UnknownKeyName（common/params.py check_key），
    # 不是返回 None。fake 必须同样抛，否则"未知 key 返回 404"的测试会在本机绿、
    # 在设备上 500——这正是第一次设备实测抓到的 bug。
    key = key.decode() if isinstance(key, bytes) else key
    self.check_key(key)
    return self._v.get(key)

  def get_bool(self, key, *a, **k):
    return bool(self._v.get(key))

  def put(self, key, value, **k):
    self._v[key.decode() if isinstance(key, bytes) else key] = value

  def remove(self, key):
    self._v.pop(key.decode() if isinstance(key, bytes) else key, None)

  def all_keys(self):
    return [k.encode() for k in self._TYPES]

  def get_type(self, key):
    key = key.decode() if isinstance(key, bytes) else key
    return self._Type(self._TYPES.get(key, "STRING"))

  def check_key(self, key):
    key = key.decode() if isinstance(key, bytes) else key
    if key not in self._TYPES:
      raise UnknownKeyName(key)
    return key


class FakeWifiManager:
  """duck-type WifiManager：记录 _init_wifi_state 调用，验证读的是缓存。"""

  class _Sec:
    name = "WPA"

  def __init__(self):
    self.init_calls: list[bool] = []
    self.networks = [
      type("Net", (), {"ssid": "home", "strength": 70, "security_type": self._Sec(), "is_tethering": False})(),
      type("Net", (), {"ssid": "weedle", "strength": 40, "security_type": None, "is_tethering": True})(),
    ]
    self.connected_ssid = "home"
    self.connecting_to_ssid = None
    self.last_error = ""

  def _init_wifi_state(self, block: bool = True):
    self.init_calls.append(block)

  def is_connection_saved(self, ssid):
    return True

  def get_ipv4_settings(self, ssid):
    return {"method": "auto", "addresses": ["10.0.0.2"], "gateway": "10.0.0.1", "dns": ["10.0.0.1"]}


@pytest.fixture
def app(monkeypatch):
  """真实 Sanic app，但 Params 与状态线程都是假的。"""
  params = FakeParams()
  monkeypatch.setattr(mod, "Params", lambda: params)
  # StatusCache.run 会起 SubMaster，测试环境里没有 msgq
  monkeypatch.setattr(mod.StatusCache, "run", lambda self, ev: None)
  monkeypatch.setattr(mod.StatusCache, "snapshot", lambda self: {"stale": True})
  monkeypatch.setattr(mod.StatusCache, "capabilities", lambda self: {"brand": "toyota"})
  monkeypatch.setattr(mod.StatusCache, "download", lambda self: None)
  # 在线标定摘要线程同理：真实 run 会起 SubMaster
  monkeypatch.setattr(mod.CalibrationController, "run_online", lambda self, ev: None)
  # Sanic 要求 app name 唯一，否则跨测试复用同一实例
  a = mod.create_app(name=f"lanlinkd_test_{os.urandom(4).hex()}")
  a.ctx.fake_params = params
  return a


class TestOpenSurface:
  """局域网单用户，无认证：所有 API 对局域网直接可达。"""

  @pytest.mark.parametrize("path", [
    "/api/params", "/api/params/_all", "/api/params/TestToggle", "/api/models",
    "/api/status", "/api/capabilities", "/api/settings_ui", "/api/logs",
    "/api/vehicle", "/api/bluetooth",
  ])
  def test_read_endpoints_need_no_token(self, app, path):
    _, r = app.test_client.get(path)
    assert r.status != 401, f"{path} unexpectedly gated"

  def test_write_endpoints_need_no_token(self, app):
    for method, path in [("put", "/api/params/TestToggle"), ("post", "/api/models/select"),
                         ("post", "/api/models/cancel"), ("post", "/api/vehicle/select"),
                         ("post", "/api/bluetooth/scan")]:
      _, r = getattr(app.test_client, method)(path, json={"value": "1"})
      assert r.status != 401, f"{method} {path} unexpectedly gated"

  def test_malformed_json_body_does_not_500(self, app):
    # Sanic 对非法 JSON 默认抛 400；装配层把它变成自己的错误信息。
    # bluetooth 的 build_payload 对垃圾 payload 返回 400 而不是 500。
    _, r = app.test_client.post("/api/bluetooth/power", json={"bad": ["payload"]}, headers={"Content-Type": "application/json"})
    assert r.status in (400, 409)
    assert r.status != 500


class TestParamsRoutes:
  def test_read_and_write_roundtrip(self, app):
    _, r = app.test_client.put("/api/params/TestToggle", json={"value": "1"})
    assert r.status == 204
    _, r = app.test_client.get("/api/params/TestToggle")
    assert r.status == 200
    assert r.json["value"] == "1"

  def test_all_params_endpoint_beats_the_key_route(self, app):
    # /api/params/_all 与 /api/params/<key> 形状相同，注册顺序错了就会被
    # 当成一个名为 "_all" 的 param 去查，返回 404
    _, r = app.test_client.get("/api/params/_all")
    assert r.status == 200
    assert isinstance(r.json, dict)
    assert "error" not in r.json

  def test_blocked_param_is_denied(self, app):
    _, r = app.test_client.put("/api/params/LanLinkEnabled", json={"value": "0"})
    # 防自锁：不能通过本 API 关掉本服务
    assert r.status == 403

  def test_unknown_param_is_404(self, app):
    _, r = app.test_client.put("/api/params/NoSuchKey", json={"value": "1"})
    assert r.status == 404

  def test_reading_unknown_param_is_404_not_500(self, app):
    # 设备实测抓到的 bug：read_param 曾直接 store.get(key)，而真实 Params.get()
    # 对未知 key 抛 UnknownKeyName → 500。fake store 当时返回 None 所以本机是绿的。
    _, r = app.test_client.get("/api/params/NoSuchKey")
    assert r.status == 404, f"unknown key leaked a {r.status}"

  def test_deleting_unknown_param_is_404_not_500(self, app):
    _, r = app.test_client.delete("/api/params/NoSuchKey")
    assert r.status == 404


class TestLegacyAvoidanceRoute:
  def test_legacy_avoidance_endpoint_is_gone(self, app):
    _, r = app.test_client.get("/api/avoidance")
    assert r.status == 404


class TestWifiCachedRead:
  """Wi-Fi 状态读 WifiManager 缓存，不再每请求阻塞初始化（spec 54）。"""

  def test_wifi_status_never_does_blocking_init(self, app):
    fake = FakeWifiManager()
    app.ctx.state._get_wifi = lambda: fake
    _, r = app.test_client.get("/api/wifi")
    assert r.status == 200
    assert r.json["connected"] == "home"
    assert [n["ssid"] for n in r.json["networks"]] == ["home"]
    # 连接页 1 Hz 轮询，每请求 _init_wifi_state(block=True) 会把 DBus 阻塞摊到每次轮询上
    assert fake.init_calls == [], "wifi 状态读取仍在走阻塞初始化"


class TestConnectivityRoute:
  """GET /api/connectivity：Wi-Fi + 蓝牙合并快照（连接页 2 请求 → 1）。"""

  def test_bluetooth_daemon_error_still_returns_200(self, app, monkeypatch):
    class BrokenClient:
      def __init__(self, timeout=None):
        pass

      def status(self):
        raise RuntimeError("bluetooth daemon unreachable")

    monkeypatch.setattr(mod, "BluetoothClient", BrokenClient)
    wifi = FakeWifiManager()
    app.ctx.state._get_wifi = lambda: wifi

    _, r = app.test_client.get("/api/connectivity")
    assert r.status == 200, "蓝牙出错把整个接口拖成非 200"
    # 蓝牙部分退化快照带 error；Wi-Fi 部分照常，且与 /api/wifi 同形同值
    _, b = app.test_client.get("/api/bluetooth")
    assert r.json["bluetooth"] == b.json
    assert "unreachable" in r.json["bluetooth"]["error"]
    _, w = app.test_client.get("/api/wifi")
    assert r.json["wifi"] == w.json


class TestBootstrapRoute:
  """GET /api/bootstrap：进「车机」页一次请求拿全三份数据（spec 通信效率）。"""

  def test_bootstrap_fields_match_the_original_endpoints(self, app):
    _, r = app.test_client.get("/api/bootstrap")
    assert r.status == 200
    body = r.json

    _, sui = app.test_client.get("/api/settings_ui")
    _, par = app.test_client.get("/api/params/_all")
    _, cap = app.test_client.get("/api/capabilities")
    assert body["settings_ui"] == sui.json
    assert body["params"] == par.json
    assert body["capabilities"] == cap.json

  def test_bootstrap_carries_params_version(self, app):
    app.ctx.fake_params._v["LanLinkParamsVersion"] = 7
    _, r = app.test_client.get("/api/bootstrap")
    assert r.json["paramsVersion"] == "7"
    # 与 /api/status 的 paramsVersion 同源同形：App 靠它判断是否重新 bootstrap
    _, st = app.test_client.get("/api/status")
    assert r.json["paramsVersion"] == st.json["paramsVersion"]


class TestCalibrationRoutes:
  def test_start_conflict_returns_409(self, app):
    import threading
    alive = threading.Event()
    ctl = app.ctx.state.calibration
    ctl._thread = threading.Thread(target=alive.wait, daemon=True)
    ctl._thread.start()
    try:
      _, r = app.test_client.post("/api/calibration/start")
      assert r.status == 409
    finally:
      alive.set()
      ctl._thread.join()
      ctl._thread = None

  def test_status_shape(self, app):
    app.ctx.state.calibration.last_result = None
    _, r = app.test_client.get("/api/calibration/status")
    assert r.status == 200
    assert r.json["running"] is False
    assert r.json["last_result"] is None

  def test_status_carries_online_calibration_summary(self, app):
    # 在线标定摘要并入安装偏移精修状态（App 精修卡 2 请求 → 1），原有字段不变
    cal = {"calStatus": "calibrated", "calPerc": 100, "calValid": True, "visionGated": False}
    app.ctx.state.calibration.online_summary = lambda: dict(cal)
    _, r = app.test_client.get("/api/calibration/status")
    assert r.json["running"] is False
    assert r.json["online"] == cal
    _, s = app.test_client.get("/api/status")
    assert "calStatus" not in s.json and "online" not in s.json

  def test_stop_returns_fit_result(self, app):
    from openpilot.selfdrive.eagled.calibrate import CalibPair
    ctl = app.ctx.state.calibration
    ctl._pairs = [CalibPair(d_radar=d, y_radar=-1.0, d_vision=d + 0.3, y_vision=-1.0, v_ego=20.0)
                  for d in (5, 10, 15, 20, 25, 30, 35, 40)]
    ctl._thread = None
    _, r = app.test_client.post("/api/calibration/stop")
    assert r.status == 200
    assert r.json["running"] is False
    assert r.json["last_result"]["n_pairs"] == 8
    assert r.json["last_result"]["camera_to_front"]["savable"] is False  # 8 对 < 30

  def _stop_with_offset(self, app, n: int, e: float):
    from openpilot.selfdrive.eagled.calibrate import CalibPair
    ctl = app.ctx.state.calibration
    ctl._pairs = [CalibPair(d_radar=5.0 + i, y_radar=-1.0, d_vision=5.0 + i + e, y_vision=-1.0, v_ego=20.0)
                  for i in range(n)]
    ctl._thread = None
    app.test_client.post("/api/calibration/stop")

  def test_apply_saves_to_params(self, app):
    self._stop_with_offset(app, 40, 0.2)
    _, r = app.test_client.post("/api/calibration/apply")
    assert r.status == 200
    assert r.json["last_result"]["saved"] is True
    assert app.ctx.fake_params._v["CameraToFront"] == pytest.approx(1.7)

  def test_apply_rejected_returns_409_without_saving(self, app):
    self._stop_with_offset(app, 10, 0.2)
    _, r = app.test_client.post("/api/calibration/apply")
    assert r.status == 409
    assert "10" in r.json["error"]
    assert "CameraToFront" not in app.ctx.fake_params._v


class TestStaticRoutes:
  def test_index_is_served(self, app):
    _, r = app.test_client.get("/")
    assert r.status == 200

  def test_path_traversal_never_serves_files_outside_static(self, app):
    # 真正要保证的性质是"STATIC_DIR 外的文件内容绝不外泄"，而不是某个具体状态码：
    # httpx 会在发包前把 `..` 规范化掉（于是打到不存在的路径得 404），
    # 而绕过规范化的编码形式则由 _serve_static 的 commonpath 检查挡掉（404）。
    for attack in ["../settings_ui.json", "../../common/params.py", "..%2f..%2fstatusd.py",
                   "....//statusd.py", "%2e%2e/settings_ui.json"]:
      _, r = app.test_client.get(f"/static/{attack}")
      assert r.status in (400, 404), f"traversal not blocked: {attack} -> {r.status}"
      # 内容层面的兜底断言：这些文件的特征串一个都不能出现在响应里
      for marker in ("BLOCKED_PARAMS", "StatusCache", "schema_version"):
        assert marker not in r.text, f"leaked {marker} via {attack}"

  def test_static_dir_escape_is_rejected_at_the_handler(self, app):
    # 直接调 handler，绕过 HTTP 客户端的 URL 规范化，验证 commonpath 检查本身。
    # 用 asyncio.run 起干净的 loop：test_client 跑完会关掉它自己的 loop。
    import asyncio
    state = app.ctx.state
    for rel in ["../statusd.py", "../../common/params.py", "../settings_ui.json"]:
      resp = asyncio.run(state._serve_static(rel))
      assert resp.status == 404, f"handler served {rel}"

  def test_nested_static_path_works(self, app):
    # ESM 子目录（js/views/*.js）必须可达，<path:path> 才能匹配多段
    _, r = app.test_client.get("/static/js/api.js")
    assert r.status in (200, 404)
    assert r.status != 405

  def test_index_is_not_cached(self, app):
    # OTA 换版后浏览器不能靠启发式缓存拿到旧 UI 配新 API
    _, r = app.test_client.get("/")
    assert r.headers.get("Cache-Control") == "no-cache"


class TestServerConfig:
  def test_single_process_is_requested(self):
    # 多 worker 会复制状态线程（StatusCache）和 WifiManager 单例，
    # NM DBus 订阅互相打架。这不是性能选项，删掉它是功能 bug，所以锁死。
    src = open(mod.__file__).read()
    assert "single_process=True" in src

  def test_body_size_is_capped(self, app):
    assert app.config.REQUEST_MAX_SIZE == mod.MAX_BODY_BYTES

  def test_daemon_refuses_to_run_when_disabled(self, monkeypatch):
    # LanLinkEnabled 关闭时必须立刻退出，不能开着对局域网监听的端口
    monkeypatch.setattr(mod, "Params", lambda: FakeParams({"LanLinkEnabled": False}))
    calls = []
    monkeypatch.setattr(mod, "create_app", lambda *a, **k: calls.append(1))
    mod.main()
    assert calls == [], "served despite LanLinkEnabled=False"

  def test_mdns_publisher_follows_the_daemon_lifecycle(self, monkeypatch):
    # 起服务拉起 _lanlink._tcp 发布；退出一起结束（广播消失，App 不连死服务）
    monkeypatch.setattr(mod, "Params", lambda: FakeParams({"LanLinkEnabled": True, "Version": "0.9.8"}))

    class FakeApp:
      def run(self, **kwargs):
        pass

    monkeypatch.setattr(mod, "create_app", lambda *a, **k: FakeApp())
    started, stopped = [], []
    monkeypatch.setattr(mod.mdns, "start", lambda *a, **k: started.append(a) or "PROC")
    monkeypatch.setattr(mod.mdns, "stop", lambda proc: stopped.append(proc))
    mod.main()
    assert len(started) == 1, "启动时没有拉起 mDNS 发布"
    assert stopped == ["PROC"], "退出时没有结束 mDNS 发布"


class TestHudStream:
  def test_streams_events_as_sse(self, app):
    state = app.ctx.state
    state.hud_events = lambda: iter([hud.sse("frame", {"stale": True}), hud.sse("heartbeat", {})])
    _, r = app.test_client.get("/api/hud/stream")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text == 'event: frame\ndata: {"stale":true}\n\nevent: heartbeat\ndata: {}\n\n'
