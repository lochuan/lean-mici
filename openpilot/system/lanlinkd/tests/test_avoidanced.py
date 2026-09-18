"""avoidanced.AvoidanceCache 的真实链路测试（发布器 → SubMaster → 快照）。"""
import threading
import time

import pytest

from openpilot.cereal import messaging
from openpilot.system.lanlinkd import avoidanced


def _publish_debug(pm: messaging.PubMaster) -> None:
  msg = messaging.new_message('avoidanceDebug')
  dbg = msg.avoidanceDebug
  dbg.valid = True
  dbg.active = True
  dbg.direction = 1
  dbg.yDes = 0.3
  dbg.bias = 0.01
  dbg.maxOffset = 0.35
  dbg.bsmRight = True
  dbg.vEgo = 25.0
  dbg.nRadar = 1
  dbg.nVision = 1
  dbg.nAssociated = 1
  dbg.edgeClearance = 999.0
  dbg.canError = False
  dbg.radarUnavailable = True
  tgts = dbg.init('targets', 2)
  tgts[0].dRel, tgts[0].yRel, tgts[0].vRel = 20.0, -1.0, 1.5
  tgts[0].matched, tgts[0].inGate, tgts[0].pairId = True, True, 1
  tgts[1].dRel, tgts[1].yRel = 25.0, -1.2
  tgts[1].cls, tgts[1].conf, tgts[1].vision = "person", 0.9, True
  tgts[1].matched, tgts[1].inGate, tgts[1].pairId = True, True, 1
  tgts[1].weight = 1.0
  pm.send('avoidanceDebug', msg)


@pytest.fixture
def publisher() -> messaging.PubMaster:
  return messaging.PubMaster(['avoidanceDebug'])


def _start_cache() -> tuple[avoidanced.AvoidanceCache, threading.Event, threading.Thread]:
  cache = avoidanced.AvoidanceCache()
  exit_event = threading.Event()
  t = threading.Thread(target=cache.run, args=(exit_event,), daemon=True)
  t.start()
  return cache, exit_event, t


def test_cache_captures_published_debug(publisher):
  assert avoidanced.STALE_AFTER_MS == 1000  # 5Hz 数据：1s 无新帧即视为停更
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):  # 5Hz 数据，多发几帧等 SubMaster 收到
      _publish_debug(publisher)
      time.sleep(0.1)
      if cache.snapshot().get("stale") is False:
        break
    snap = cache.snapshot()
  finally:
    exit_event.set()
    t.join(timeout=2)

  assert snap["stale"] is False
  assert snap["logMonoTime"] > 0
  assert snap["valid"] is True
  assert snap["active"] is True
  assert snap["direction"] == 1
  assert snap["yDes"] == pytest.approx(0.3)
  assert snap["maxOffset"] == pytest.approx(0.35)
  assert snap["bsmRight"] is True
  assert snap["vEgo"] == pytest.approx(25.0)
  assert snap["edgeClearance"] == pytest.approx(999.0)
  assert snap["canError"] is False
  assert snap["radarUnavailable"] is True
  assert (snap["nRadar"], snap["nVision"], snap["nAssociated"]) == (1, 1, 1)
  assert len(snap["targets"]) == 2
  radar_t, vision_t = snap["targets"]
  assert vision_t["cls"] == "person" and vision_t["vision"] is True
  assert radar_t["vision"] is False and radar_t["vRel"] == pytest.approx(1.5)
  # 配对双方共享同一 pairId
  assert vision_t["pairId"] == radar_t["pairId"] == 1


def test_cache_goes_stale_after_silence(publisher, monkeypatch):
  monkeypatch.setattr(avoidanced, "STALE_AFTER_MS", 200)
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(10):
      _publish_debug(publisher)
      time.sleep(0.05)
      if cache.snapshot().get("stale") is False:
        break
    assert cache.snapshot()["stale"] is False
    time.sleep(0.4)  # 静默超过 STALE_AFTER_MS
    assert cache.snapshot()["stale"] is True
  finally:
    exit_event.set()
    t.join(timeout=2)
