import threading
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.system.lanlinkd import statusd
from openpilot.system.lanlinkd.status_snapshot import STAGE_FIELDS
from openpilot.system.lanlinkd.statusd import StatusCache


def make_fake_submaster_cls(exit_event, stop_after):
  state = {"calls": 0}

  class FakeSubMaster:
    def __init__(self, services):
      self.updated = dict.fromkeys(services, False)

    def update(self, timeout):
      state["calls"] += 1
      if state["calls"] >= stop_after:
        exit_event.set()

    def __getitem__(self, name):
      return NS()

  return FakeSubMaster


class TestStatusdRun:
  def test_loop_survives_snapshot_exception(self):
    exit_event = threading.Event()
    state = {"ok": False}
    fake_cls = make_fake_submaster_cls(exit_event, stop_after=3)

    def flaky_snapshot(services, version_info, caps):
      if not state["ok"]:
        state["ok"] = True
        raise RuntimeError("boom")
      return {"stale": False}

    cache = StatusCache({"Version": "0.11.2"}, "tici",
                        params=NS(get=lambda k: None, get_bool=lambda k: False))
    with patch.object(statusd.messaging, "SubMaster", fake_cls), \
         patch.object(statusd, "build_snapshot", flaky_snapshot), \
         patch.object(statusd, "build_capabilities", lambda *a, **k: {}):
      cache.run(exit_event)

    assert state["ok"] is True
    assert cache.snapshot()["stale"] is False

  def test_submaster_init_failure_returns(self):
    def boom(services):
      raise RuntimeError("ctor failed")

    cache = StatusCache({"Version": "0.11.2"}, "tici",
                        params=NS(get=lambda k: None, get_bool=lambda k: False))
    with patch.object(statusd.messaging, "SubMaster", boom):
      cache.run(threading.Event())


  def test_model_status_from_frames(self):
    exit_event = threading.Event()
    n = 60

    class FakeSubMaster:
      def __init__(self, services):
        self.updated = dict.fromkeys(services, False)
        self.calls = 0

      def update(self, timeout):
        self.calls += 1
        self.updated["modelV2"] = True
        self.updated["modelDataV2SP"] = True
        if self.calls >= n:
          exit_event.set()

      def __getitem__(self, name):
        sp = NS(bigStages=NS(**dict.fromkeys(STAGE_FIELDS, 0.)), bigSource='timeout', bigLateReplyCount=0,
                bigDeadlineMs=70.)
        return {"modelV2": NS(big=self.calls > 55), "modelDataV2SP": sp}.get(name, NS())

    params = NS(get=lambda k: "connected" if k == "BigmodelLinkState" else None, get_bool=lambda k: k == "BigmodelToggle")
    cache = StatusCache({}, "tici", params=params)
    with patch.object(statusd.messaging, "SubMaster", FakeSubMaster), \
         patch.object(statusd, "build_snapshot", lambda *a: {}), \
         patch.object(statusd, "build_capabilities", lambda *a, **k: {}):
      cache.run(exit_event)

    model = cache.snapshot()["model"]
    # lanlink 只回答「大模型在不在工作」：开关、链路、最近 50 帧来源
    timing = model.pop("timing")
    assert model == {"bigEnabled": True, "linkState": "connected", "frames": [0] * 45 + [1] * 5}
    # 分段/原因窗口 = 最近 100 帧 modelDataV2SP
    assert timing["window"] == 60 and timing["sources"]["timeout"] == 60 and timing["deadlineMs"] == 70.


class TestTorqueStatus:
  @staticmethod
  def _ltp(laf):
    return NS(valid=True, useParams=True, latAccelFactorFiltered=laf, frictionCoefficientFiltered=0.1,
              latAccelOffsetFiltered=0., latAccelFactorRaw=laf, frictionCoefficientRaw=0.1, calPerc=100,
              totalBucketPoints=4000., decay=50., maxResets=1., speedBinCenters=[], speedBinLatAccelFactors=[],
              speedBinFrictions=[], speedBinValid=[], speedBinCalPerc=[])

  def _run(self, live_calls, cache_laf, clock=None):
    exit_event = threading.Event()
    ltp = self._ltp(2.2)

    class FakeSubMaster:
      def __init__(self, services):
        self.updated = dict.fromkeys(services, False)
        self.calls = 0

      def update(self, timeout):
        self.calls += 1
        self.updated["lateralTorqueParameters"] = self.calls in live_calls
        if clock is not None:
          clock["t"] += 10.0  # 每轮推进 10s（> TORQUE_LIVE_TIMEOUT）
        if self.calls >= 2:
          exit_event.set()

      def __getitem__(self, name):
        return ltp if name == "lateralTorqueParameters" else NS()

    cache_bytes = b"cache"
    params = NS(get=lambda k: cache_bytes if k == "LiveTorqueParameters" else None,
                get_bool=lambda k: k == "SpeedDependentTorqueToggle")
    cache = StatusCache({}, "tici", params=params)
    with patch.object(statusd.messaging, "SubMaster", FakeSubMaster), \
         patch.object(statusd, "build_snapshot", lambda *a: {}), \
         patch.object(statusd, "build_capabilities", lambda *a, **k: {}), \
         patch.object(statusd, "torque_params_from_cache", lambda b: statusd.torque_params_dict(self._ltp(cache_laf))), \
         patch.object(statusd.time, "monotonic", (lambda: clock["t"]) if clock is not None else statusd.time.monotonic):
      cache.run(exit_event)
    return cache.snapshot()["torque"]

  def test_live_message_wins(self):
    t = self._run(live_calls={1, 2}, cache_laf=2.9)
    assert t["source"] == "live" and t["learned"]["latAccelFactor"] == 2.2
    assert t["toggles"] == {"EnforceTorqueControl": False, "LiveTorqueParamsToggle": False, "SpeedDependentTorqueToggle": True}

  def test_parked_falls_back_to_saved_cache(self):
    t = self._run(live_calls=set(), cache_laf=2.9)
    assert t["source"] == "cache" and t["learned"]["latAccelFactor"] == 2.9

  def test_stale_live_falls_back_to_cache(self):
    # 第 1 轮收到实时消息，第 2 轮没有且已过 10s：torqued 停了（熄火），改读落盘缓存
    t = self._run(live_calls={1}, cache_laf=2.9, clock={"t": 0.0})
    assert t["source"] == "cache"


class TestDiagnostics:
  def _run(self, steps, params):
    """steps：每轮 SubMaster.update 后置为 updated 的服务集合；消息由 msgs 提供。"""
    exit_event = threading.Event()
    msgs = {
      "deviceState": NS(started=True),
      "carState": NS(vEgo=20.0, leftBlinker=False, rightBlinker=False, steeringPressed=False,
                     steerFaultTemporary=False, steerFaultPermanent=False, steeringAngleOffsetDeg=0.0),
      "carControl": NS(latActive=True, enabled=True),
      "controlsState": NS(desiredCurvature=0.0, curvature=0.0,
                          lateralControlState=NS(which=lambda: "torqueState", torqueState=NS(saturated=False, output=0.1))),
      "modelV2": NS(big=False),
    }

    class FakeSubMaster:
      def __init__(self, services):
        self.updated = dict.fromkeys(services, False)
        self.calls = 0

      def update(self, timeout):
        for k in self.updated:
          self.updated[k] = k in steps[self.calls]
        self.calls += 1
        if self.calls >= len(steps):
          exit_event.set()

      def __getitem__(self, name):
        return msgs.get(name, NS())

    cache = StatusCache({}, "tici", params=params)
    with patch.object(statusd.messaging, "SubMaster", FakeSubMaster), \
         patch.object(statusd, "build_snapshot", lambda *a: {}), \
         patch.object(statusd, "build_capabilities", lambda *a, **k: {}):
      cache.run(exit_event)
    return cache

  def test_loop_feeds_drive_diagnostics(self):
    params = NS(get=lambda k: None, get_bool=lambda k: False)
    steps = [{"deviceState"}] + [{"carState", "carControl", "controlsState"}] * 10
    report = self._run(steps, params).diagnostics()
    assert report["drive"]["started"] is True
    assert report["steering"]["control"] == "torque"
    assert report["steering"]["activeSeconds"] > 0

  def test_broken_message_does_not_stop_status_loop(self):
    # modelV2 缺车道线等字段：诊断统计抛异常，但状态快照仍照常产出
    params = NS(get=lambda k: None, get_bool=lambda k: False)
    cache = self._run([{"deviceState"}, {"modelV2"}, {"modelV2"}], params)
    assert "torque" in cache.snapshot()
    assert cache.diagnostics()["drift"]["seconds"] == 0
