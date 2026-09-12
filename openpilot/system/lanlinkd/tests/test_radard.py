"""radard.RadarCache 的真实链路测试。

实车炸过的回归：radard.py 曾把 sm['radarTracks']（内层 RadarTracks
结构体）当 Event 访问 .logMonoTime，每帧 AttributeError 被吞掉，快照
永远 stale，雷达点阵在实车上不显示。装配层测试把 RadarCache.run 整个
mock 掉，测不到这条路径——本文件用真实 cereal 消息跑通
发布器 → SubMaster → 快照 的完整链路（zmq/msgq 后端同进程皆可）。
"""
import threading
import time

import pytest

from openpilot.cereal import messaging
from openpilot.system.lanlinkd import radard


def _publish_tracks(pm: messaging.PubMaster, n_points: int, d_rel: float) -> None:
  msg = messaging.new_message('radarTracks')
  pts = msg.radarTracks.init('points', n_points)
  for i, pt in enumerate(pts):
    pt.trackId = i
    pt.dRel = d_rel + i
    pt.yRel = 1.5 * i - 1.0
    pt.vRel = 0.5 * i
  pm.send('radarTracks', msg)


@pytest.fixture
def publisher() -> messaging.PubMaster:
  return messaging.PubMaster(['radarTracks'])


def _start_cache() -> tuple[radard.RadarCache, threading.Event, threading.Thread]:
  cache = radard.RadarCache()
  exit_event = threading.Event()
  t = threading.Thread(target=cache.run, args=(exit_event,), daemon=True)
  t.start()
  return cache, exit_event, t


def test_cache_captures_published_tracks(publisher):
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):  # 20Hz 发 1 秒，等 SubMaster 收到
      _publish_tracks(publisher, 2, 30.0)
      time.sleep(0.05)
    snap = cache.snapshot()
  finally:
    exit_event.set()
    t.join(timeout=2)

  assert snap["stale"] is False
  assert snap["logMonoTime"] > 0
  assert [p["trackId"] for p in snap["points"]] == [0, 1]
  assert snap["points"][0]["dRel"] == pytest.approx(30.0)
  assert snap["points"][1]["yRel"] == pytest.approx(0.5)
  assert snap["errors"] == {"canError": False, "radarUnavailableTemporary": False}


def test_cache_goes_stale_after_silence(publisher, monkeypatch):
  monkeypatch.setattr(radard, "STALE_AFTER_MS", 200)
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(10):
      _publish_tracks(publisher, 1, 10.0)
      time.sleep(0.05)
    assert cache.snapshot()["stale"] is False
    time.sleep(0.4)  # 静默超过 STALE_AFTER_MS
    assert cache.snapshot()["stale"] is True
  finally:
    exit_event.set()
    t.join(timeout=2)
