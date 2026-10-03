import threading
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.system.lanlinkd import statusd
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


  def test_model_status_from_frames_and_server_telemetry(self):
    exit_event = threading.Event()
    n = 60

    class FakeSubMaster:
      def __init__(self, services):
        self.updated = dict.fromkeys(services, False)
        self.calls = 0

      def update(self, timeout):
        self.calls += 1
        self.updated["modelV2"] = True
        self.updated["bigModelReply"] = True
        if self.calls >= n:
          exit_event.set()

      def __getitem__(self, name):
        big = self.calls > 55
        late = 40 < self.calls <= 50
        return {
          "modelV2": NS(big=big, modelExecutionTime=0.01, frameDropPerc=1.5),
          "modelDataV2SP": NS(bigLatencyMs=40. if big else 0., bigLateReplyMs=90. if late else 0., bigLateReplyCount=2 if late else 0, cameraToModelMs=20.),
          "bigModelReply": NS(telemetry=[1000, 2000, 3000, 6000]),
        }.get(name, NS())

    params = NS(get=lambda k: "connected" if k == "BigmodelLinkState" else None, get_bool=lambda k: k == "BigmodelToggle")
    cache = StatusCache({}, "tici", params=params)
    with patch.object(statusd.messaging, "SubMaster", FakeSubMaster), \
         patch.object(statusd, "build_snapshot", lambda *a: {}), \
         patch.object(statusd, "build_capabilities", lambda *a, **k: {}):
      cache.run(exit_event)

    model = cache.snapshot()["model"]
    assert model["bigEnabled"] is True and model["linkState"] == "connected"
    assert len(model["frames"]) == 50
    assert model["frames"][-5:] == [1] * 5 and model["frames"][-6] == 0
    assert model["execAvgMs"] == 10. and model["frameDropPerc"] == 1.5
    # 最近 50 帧：按时 5、迟到 10 帧各 2 个、其余没回；往返均/峰把迟到的也算进去 (5×40 + 10×90) / 15
    assert (model["replyOnTime"], model["replyLate"], model["replyMissing"]) == (5, 20, 25)
    assert model["bigLatencyAvgMs"] == 73.33 and model["bigLatencyMaxMs"] == 90.
    assert model["serverMs"] == {"recv": 1., "prep": 2., "htp": 3., "total": 6.}
