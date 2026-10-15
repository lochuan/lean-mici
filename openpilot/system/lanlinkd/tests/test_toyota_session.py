import pytest

from openpilot.system.lanlinkd.toyota.session import DIAG_MODE_PARAM, DiagSession, SessionError, car_is_parked

from .fake_params import FakeParams

PARK = 1


class FakeLink:
  def __init__(self):
    self.closed = False

  def close(self):
    self.closed = True


class Harness:
  """session 的外部依赖全部可控：pandad 何时退出、时钟、链路。"""

  def __init__(self, offroad=True, parked=False, pandad_exits_after=1):
    self.params = FakeParams({"IsOffroad": offroad})
    self.parked = parked
    self.pandad_polls = 0
    self.pandad_exits_after = pandad_exits_after
    self.now = 1000.0
    self.links: list[FakeLink] = []
    self.session = DiagSession(
      self.params, open_link=self._open_link, pandad_running=self._pandad_running,
      is_parked=lambda: self.parked, clock=lambda: self.now, sleep=self._sleep,
      idle_timeout_s=900, pandad_stop_timeout_s=15,
    )

  def _open_link(self):
    self.links.append(FakeLink())
    return self.links[-1]

  def _pandad_running(self):
    self.pandad_polls += 1
    return self.pandad_exits_after is None or self.pandad_polls <= self.pandad_exits_after

  def _sleep(self, seconds):
    self.now += seconds


class TestEnter:
  def test_offroad_enter_stops_pandad_then_opens_link(self):
    h = Harness(offroad=True)
    h.session.enter()
    assert h.params.get_bool(DIAG_MODE_PARAM)
    assert len(h.links) == 1
    assert h.session.snapshot()["state"] == "active"

  def test_onroad_requires_park(self):
    h = Harness(offroad=False, parked=False)
    with pytest.raises(SessionError) as err:
      h.session.enter()
    assert err.value.status == 409
    assert not h.params.get_bool(DIAG_MODE_PARAM)
    assert h.links == []

  def test_onroad_parked_is_allowed(self):
    h = Harness(offroad=False, parked=True)
    h.session.enter()
    assert h.session.snapshot()["state"] == "active"

  def test_pandad_that_never_stops_rolls_back(self):
    h = Harness(pandad_exits_after=None)
    with pytest.raises(SessionError) as err:
      h.session.enter()
    assert err.value.status == 504
    # 回滚：param 清掉让 manager 把 pandad 留着，不打开 panda
    assert not h.params.get_bool(DIAG_MODE_PARAM)
    assert h.links == []
    snap = h.session.snapshot()
    assert snap["state"] == "idle" and "pandad" in snap["error"]

  def test_open_link_failure_rolls_back(self):
    h = Harness()

    def broken():
      raise OSError("spi busy")
    h.session._open_link = broken
    with pytest.raises(SessionError) as err:
      h.session.enter()
    assert err.value.status == 500
    assert not h.params.get_bool(DIAG_MODE_PARAM)

  def test_enter_twice_is_idempotent(self):
    h = Harness()
    h.session.enter()
    h.session.enter()
    assert len(h.links) == 1


class TestExit:
  def test_exit_closes_link_and_restores_pandad(self):
    h = Harness()
    h.session.enter()
    h.session.exit()
    assert h.links[0].closed
    assert not h.params.get_bool(DIAG_MODE_PARAM)
    assert h.session.snapshot()["state"] == "idle"

  def test_idle_timeout_exits(self):
    h = Harness()
    h.session.enter()
    h.now += 899
    h.session.expire_if_idle()
    assert h.session.snapshot()["state"] == "active"
    h.now += 2
    h.session.expire_if_idle()
    assert h.session.snapshot()["state"] == "idle"
    assert h.links[0].closed

  def test_snapshot_reports_remaining_idle_time(self):
    # 设备直接给剩余秒数，网页不用拿浏览器时钟去减设备时钟
    h = Harness()
    assert "idleRemainingS" not in h.session.snapshot()
    h.session.enter()
    h.now += 100
    assert h.session.snapshot()["idleRemainingS"] == 800

  def test_reset_on_startup_clears_stale_param(self):
    # lanlinkd 崩溃重启时 param 可能还在，不清掉 pandad 就永远起不来
    h = Harness()
    h.params.put_bool(DIAG_MODE_PARAM, True)
    h.session.reset_on_startup()
    assert not h.params.get_bool(DIAG_MODE_PARAM)


class TestRun:
  def test_run_requires_active_session(self):
    h = Harness()
    with pytest.raises(SessionError) as err:
      h.session.run(lambda link: None)
    assert err.value.status == 409

  def test_run_passes_link_and_refreshes_activity(self):
    h = Harness()
    h.session.enter()
    h.now += 800
    assert h.session.run(lambda link: link) is h.links[0]
    h.now += 800
    h.session.expire_if_idle()
    assert h.session.snapshot()["state"] == "active"
    assert h.session.snapshot()["lastActivity"] == pytest.approx(h.now - 800)


class TestCarIsParked:
  def test_park_and_standstill(self):
    assert car_is_parked({"stale": False, "car": {"gearShifter": PARK, "standstill": True, "vEgo": 0.0}})

  @pytest.mark.parametrize("snap", [
    {"stale": True, "car": {"gearShifter": PARK, "standstill": True, "vEgo": 0.0}},
    {"stale": False, "car": {"gearShifter": 2, "standstill": True, "vEgo": 0.0}},
    {"stale": False, "car": {"gearShifter": PARK, "standstill": False, "vEgo": 0.5}},
    {"stale": False},
  ])
  def test_anything_else_is_not_parked(self, snap):
    assert not car_is_parked(snap)
