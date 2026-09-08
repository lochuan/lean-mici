import threading
from types import SimpleNamespace as NS
from unittest.mock import patch

from openpilot.system.lanlinkd import statusd
from openpilot.system.lanlinkd.statusd import StatusCache


def make_fake_submaster_cls(exit_event, stop_after):
  state = {"calls": 0}

  class FakeSubMaster:
    def __init__(self, services):
      self.updated = {name: False for name in services}

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

    cache = StatusCache({"Version": "0.11.2"}, "tici")
    with patch.object(statusd.messaging, "SubMaster", fake_cls), \
         patch.object(statusd, "build_snapshot", flaky_snapshot), \
         patch.object(statusd, "build_capabilities", lambda *a, **k: {}):
      cache.run(exit_event)

    assert state["ok"] is True
    assert cache.snapshot() == {"stale": False}

  def test_submaster_init_failure_returns(self):
    def boom(services):
      raise RuntimeError("ctor failed")

    cache = StatusCache({"Version": "0.11.2"}, "tici")
    with patch.object(statusd.messaging, "SubMaster", boom):
      cache.run(threading.Event())
