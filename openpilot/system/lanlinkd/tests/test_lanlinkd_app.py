"""lanlinkd 装配层测试（Sanic）。

aiohttp 时代这一层零测试——框架迁移最容易改错的恰恰是认证中间件语义和
路由匹配，而不是被测得很好的纯逻辑模块。这里用 Sanic 自带的 test_client
跑真实 HTTP，覆盖：公开面、token 准入、路径参数、静态文件穿越防护。
"""
import json
import os
from unittest.mock import patch

import pytest

from openpilot.common.params import UnknownKeyName
from openpilot.system.lanlinkd import lanlinkd as mod


class FakeParams:
  """duck-type Params：BOOL/STRING/INT 足够覆盖装配层。

  get_type 必须返回带 .name 的枚举式对象，不能返回裸字符串：真实 Params
  返回 ParamKeyType 枚举，params_api.type_name 先看 .name、否则 int(raw)，
  裸字符串会让它 ValueError。这个 fake 必须和真实契约一致，否则测试绿了
  但设备上 500。
  """

  _TYPES = {"LanLinkEnabled": "BOOL", "LanLinkPasswordHash": "STRING",
            "LanLinkParamsVersion": "INT", "TestToggle": "BOOL",
            # LanlinkApp.__init__ 读这三个组版本信息；设备上它们都存在
            "Version": "STRING", "GitBranch": "STRING", "GitCommit": "STRING"}

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
  # RadarCache 同理：真实 run 会起 SubMaster
  monkeypatch.setattr(mod.RadarCache, "run", lambda self, ev: None)
  monkeypatch.setattr(mod.RadarCache, "snapshot", lambda self: {"stale": True})
  # Sanic 要求 app name 唯一，否则跨测试复用同一实例
  a = mod.create_app(name=f"lanlinkd_test_{os.urandom(4).hex()}")
  a.ctx.fake_params = params
  return a


def _token(app) -> str:
  """走真实 setup+login 拿 token，而不是伪造 session。"""
  _, r = app.test_client.post("/api/setup", json={"password": "secret123"})
  assert r.status == 204, r.text
  _, r = app.test_client.post("/api/login", json={"password": "secret123"})
  assert r.status == 200, r.text
  return r.json["token"]


def _auth(token: str) -> dict:
  return {"Authorization": f"Bearer {token}"}


class TestPublicSurface:
  def test_login_and_setup_are_public(self, app):
    # 未登录也必须能打到这两个 handler（否则首次配置无从下手）
    _, r = app.test_client.post("/api/setup", json={"password": "secret123"})
    assert r.status == 204
    _, r = app.test_client.post("/api/login", json={"password": "secret123"})
    assert r.status == 200
    assert "token" in r.json

  def test_public_surface_is_exactly_index_login_setup(self):
    # 这张表就是全部公开面。新增公开路径必须同步改这个断言，
    # 避免"顺手加个公开接口"绕过认证而无人察觉。
    assert mod.PUBLIC_PATHS == ("/", "/api/login", "/api/setup")
    assert mod.PUBLIC_PREFIXES == ("/static/", "/assets/")

  @pytest.mark.parametrize("path", [
    "/api/params", "/api/params/_all", "/api/params/TestToggle", "/api/models",
    "/api/status", "/api/capabilities", "/api/settings_ui", "/api/logs", "/api/password",
    "/api/vehicle", "/api/radar", "/api/bluetooth",
  ])
  def test_every_other_endpoint_requires_token(self, app, path):
    _, r = app.test_client.get(path)
    assert r.status == 401, f"{path} leaked without a token"
    assert r.json["error"] == "unauthorized"

  def test_write_endpoints_require_token(self, app):
    for method, path in [("put", "/api/params/TestToggle"), ("post", "/api/models/select"),
                         ("post", "/api/models/cancel"), ("post", "/api/vehicle/select"),
                         ("post", "/api/bluetooth/scan")]:
      _, r = getattr(app.test_client, method)(path, json={"value": "1"})
      assert r.status == 401, f"{method} {path} leaked without a token"
    # DELETE 单独发：sanic_testing 的 delete() 不接受 json=
    _, r = app.test_client.delete("/api/params/TestToggle")
    assert r.status == 401, "DELETE /api/params leaked without a token"

  def test_garbage_token_rejected(self, app):
    _token(app)
    _, r = app.test_client.get("/api/status", headers=_auth("not-a-real-token"))
    assert r.status == 401

  def test_malformed_authorization_header_rejected(self, app):
    t = _token(app)
    for header in [t, f"Basic {t}", f"bearer {t}", ""]:
      _, r = app.test_client.get("/api/status", headers={"Authorization": header})
      assert r.status == 401, f"accepted malformed header: {header!r}"


class TestAuthFlow:
  def test_setup_rejects_short_password(self, app):
    _, r = app.test_client.post("/api/setup", json={"password": "abc"})
    assert r.status == 400

  def test_setup_is_once_only(self, app):
    app.test_client.post("/api/setup", json={"password": "secret123"})
    _, r = app.test_client.post("/api/setup", json={"password": "other123"})
    assert r.status == 409

  def test_login_wrong_password(self, app):
    app.test_client.post("/api/setup", json={"password": "secret123"})
    _, r = app.test_client.post("/api/login", json={"password": "wrong123"})
    assert r.status == 401

  def test_login_before_setup(self, app):
    _, r = app.test_client.post("/api/login", json={"password": "secret123"})
    assert r.status == 409

  def test_lockout_after_repeated_failures(self, app):
    app.test_client.post("/api/setup", json={"password": "secret123"})
    for _ in range(5):
      app.test_client.post("/api/login", json={"password": "wrong123"})
    _, r = app.test_client.post("/api/login", json={"password": "secret123"})
    # 即使密码正确也必须被锁——否则防爆破形同虚设
    assert r.status == 429

  def test_password_change_revokes_existing_sessions(self, app):
    t = _token(app)
    _, r = app.test_client.post("/api/password", json={"old": "secret123", "new": "newsecret1"}, headers=_auth(t))
    assert r.status == 204
    _, r = app.test_client.get("/api/status", headers=_auth(t))
    assert r.status == 401, "old token still valid after password change"

  def test_password_change_needs_correct_old(self, app):
    t = _token(app)
    _, r = app.test_client.post("/api/password", json={"old": "nope1234", "new": "newsecret1"}, headers=_auth(t))
    assert r.status == 401

  def test_malformed_json_body_does_not_500(self, app):
    # Sanic 对非法 JSON 默认抛 400；装配层把它变成自己的错误信息
    _, r = app.test_client.post("/api/setup", data="not json", headers={"Content-Type": "application/json"})
    assert r.status == 400
    assert r.status != 500


class TestParamsRoutes:
  def test_read_and_write_roundtrip(self, app):
    t = _token(app)
    _, r = app.test_client.put("/api/params/TestToggle", json={"value": "1"}, headers=_auth(t))
    assert r.status == 204
    _, r = app.test_client.get("/api/params/TestToggle", headers=_auth(t))
    assert r.status == 200
    assert r.json["value"] == "1"

  def test_all_params_endpoint_beats_the_key_route(self, app):
    # /api/params/_all 与 /api/params/<key> 形状相同，注册顺序错了就会被
    # 当成一个名为 "_all" 的 param 去查，返回 404
    t = _token(app)
    _, r = app.test_client.get("/api/params/_all", headers=_auth(t))
    assert r.status == 200
    assert isinstance(r.json, dict)
    assert "error" not in r.json

  def test_blocked_param_is_denied(self, app):
    t = _token(app)
    _, r = app.test_client.put("/api/params/LanLinkEnabled", json={"value": "0"}, headers=_auth(t))
    # 防自锁：不能通过本 API 关掉本服务
    assert r.status == 403

  def test_unknown_param_is_404(self, app):
    t = _token(app)
    _, r = app.test_client.put("/api/params/NoSuchKey", json={"value": "1"}, headers=_auth(t))
    assert r.status == 404

  def test_reading_unknown_param_is_404_not_500(self, app):
    # 设备实测抓到的 bug：read_param 曾直接 store.get(key)，而真实 Params.get()
    # 对未知 key 抛 UnknownKeyName → 500。fake store 当时返回 None 所以本机是绿的。
    t = _token(app)
    _, r = app.test_client.get("/api/params/NoSuchKey", headers=_auth(t))
    assert r.status == 404, f"unknown key leaked a {r.status}"

  def test_deleting_unknown_param_is_404_not_500(self, app):
    t = _token(app)
    _, r = app.test_client.delete("/api/params/NoSuchKey", headers=_auth(t))
    assert r.status == 404


class TestRadarRoute:
  def test_radar_returns_cached_snapshot(self, app):
    t = _token(app)
    fake = {
      "stale": False,
      "logMonoTime": 1234567890,
      "points": [
        {"trackId": 0, "dRel": 26.89, "yRel": 0.04, "vRel": 1.80},
        {"trackId": 1, "dRel": 7.20, "yRel": 0.08, "vRel": 1.75},
      ],
      "errors": {"canError": False, "radarUnavailableTemporary": False},
    }
    app.ctx.state.radar.snapshot = lambda: fake
    _, r = app.test_client.get("/api/radar", headers=_auth(t))
    assert r.status == 200
    assert r.json == fake

  def test_radar_stale_shape(self, app):
    # 无数据时（熄火/无雷达平台）必须返回 {"stale": true}，而不是 500 或空体
    t = _token(app)
    app.ctx.state.radar.snapshot = lambda: {"stale": True}
    _, r = app.test_client.get("/api/radar", headers=_auth(t))
    assert r.status == 200
    assert r.json == {"stale": True}


class TestStaticRoutes:
  def test_index_is_served(self, app):
    _, r = app.test_client.get("/")
    assert r.status == 200

  def test_path_traversal_never_serves_files_outside_static(self, app):
    # 真正要保证的性质是"STATIC_DIR 外的文件内容绝不外泄"，而不是某个具体状态码：
    # httpx 会在发包前把 `..` 规范化掉（于是打到非公开路径得 401），
    # 而绕过规范化的编码形式则由 _serve_static 的 commonpath 检查挡掉（404）。
    for attack in ["../settings_ui.json", "../../common/params.py", "..%2f..%2fauth.py",
                   "....//auth.py", "%2e%2e/settings_ui.json"]:
      _, r = app.test_client.get(f"/static/{attack}")
      assert r.status in (400, 401, 404), f"traversal not blocked: {attack} -> {r.status}"
      # 内容层面的兜底断言：这些文件的特征串一个都不能出现在响应里
      for marker in ("BLOCKED_PARAMS", "PBKDF2_ITERATIONS", "schema_version"):
        assert marker not in r.text, f"leaked {marker} via {attack}"

  def test_static_dir_escape_is_rejected_at_the_handler(self, app):
    # 直接调 handler，绕过 HTTP 客户端的 URL 规范化，验证 commonpath 检查本身。
    # 用 asyncio.run 起干净的 loop：test_client 跑完会关掉它自己的 loop。
    import asyncio
    state = app.ctx.state
    for rel in ["../auth.py", "../../common/params.py", "../settings_ui.json"]:
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
    # 多 worker 会把 SessionStore/LoginThrottle 按进程分裂，登录随机失效。
    # 这不是性能选项，删掉它是功能 bug，所以锁死。
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
