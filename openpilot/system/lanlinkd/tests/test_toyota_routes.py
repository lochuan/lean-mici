"""丰田诊断路由的装配层测试：真实 Sanic + 假 panda/pandad。"""
import pytest

from .test_lanlinkd_app import app as base_app  # noqa: F401  复用 app fixture


class FakeLink:
  closed = False

  def close(self):
    self.closed = True


@pytest.fixture
def app(base_app):  # noqa: F811
  # session 在 create_app 时已建好，直接替换它的外部依赖
  link = FakeLink()
  base_app.ctx.state.toyota.session._open_link = lambda: link
  base_app.ctx.state.toyota.session._pandad_running = lambda: False
  base_app.ctx.link = link
  return base_app


class TestSessionRoutes:
  def test_initial_state_is_idle(self, app):
    _, r = app.test_client.get("/api/toyota/session")
    assert r.status == 200
    assert r.json["state"] == "idle"

  def test_enter_while_driving_is_409(self, app):
    app.ctx.fake_params.put_bool("IsOffroad", False)
    _, r = app.test_client.post("/api/toyota/session/enter")
    assert r.status == 409
    assert "P 挡" in r.json["error"]

  def test_enter_offroad_then_exit(self, app):
    app.ctx.fake_params.put_bool("IsOffroad", True)
    _, r = app.test_client.post("/api/toyota/session/enter")
    assert r.status == 200
    assert r.json["state"] == "active"
    assert app.ctx.fake_params.get_bool("LanLinkDiagMode")
    _, r = app.test_client.post("/api/toyota/session/exit")
    assert r.status == 204
    assert app.ctx.link.closed
    assert not app.ctx.fake_params.get_bool("LanLinkDiagMode")

  def test_diag_mode_param_cannot_be_set_through_params_api(self, app):
    # 绕过 session 直接置 param 会停 pandad 却没有人接管 panda
    _, r = app.test_client.put("/api/params/LanLinkDiagMode", json={"value": "1"})
    assert r.status == 403


class TestRequiresSession:
  @pytest.mark.parametrize("method,path", [
    ("post", "/api/toyota/customizations/scan"),
    ("post", "/api/toyota/customizations/car_setting_warn_headlights_on_door_open/write"),
    ("post", "/api/toyota/backups/0123456789ab/restore"),
    ("post", "/api/toyota/dtc/scan"),
    ("post", "/api/toyota/dtc/clear"),
    ("post", "/api/toyota/live/read"),
  ])
  def test_bus_operations_need_diag_mode(self, app, method, path):
    _, r = getattr(app.test_client, method)(path, json={"ecu": "750/40", "value": 0, "before": "00", "ecus": ["750/40"], "keys": ["car_tool_mileage"]})
    assert r.status in (404, 409)
    assert "error" in r.json

  def test_backups_listing_works_without_session(self, app, tmp_path):
    app.ctx.state.toyota.backups.directory = str(tmp_path)
    _, r = app.test_client.get("/api/toyota/backups")
    assert r.status == 200
    assert r.json == {"backups": []}


class TestCatalogRoute:
  def test_catalog_works_without_session(self, app):
    _, r = app.test_client.get("/api/toyota/catalog")
    assert r.status == 200
    assert len(r.json["settings"]) == 130

