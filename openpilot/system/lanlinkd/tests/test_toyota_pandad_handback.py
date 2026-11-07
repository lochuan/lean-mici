"""诊断模式的 panda 必须总能交还给 pandad：lanlinkd 被停或退出时都不能把 pandad 留在停止状态。"""
from openpilot.system.manager import process_config

from .test_lanlinkd_app import app as base_app  # noqa: F401  app fixture 依赖它
from .test_toyota_routes import app  # noqa: F401  复用带假 panda 的 app fixture


class Params(dict):
  def get_bool(self, key):
    return bool(self.get(key))


class TestPandadGate:
  def test_pandad_yields_only_while_lanlink_runs_a_diag_session(self):
    assert process_config.pandad_run(False, Params(), None)
    assert not process_config.pandad_run(False, Params(LanLinkEnabled=True, LanLinkDiagMode=True), None)

  def test_turning_lanlink_off_hands_panda_back(self):
    # 关 LanLink → manager 停掉 lanlinkd，没人再清 LanLinkDiagMode；pandad 不能因此一直不起
    assert process_config.pandad_run(False, Params(LanLinkEnabled=False, LanLinkDiagMode=True), None)


class TestShutdown:
  def test_close_exits_diag_session(self, app):  # noqa: F811
    app.ctx.fake_params.put_bool("IsOffroad", True)
    _, r = app.test_client.post("/api/toyota/session/enter")
    assert r.status == 200
    app.ctx.state.close()
    assert app.ctx.link.closed
    assert not app.ctx.fake_params.get_bool("LanLinkDiagMode")
    assert app.ctx.state.exit_event.is_set()
