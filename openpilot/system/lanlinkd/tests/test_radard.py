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


def _publish_tracks(pm: messaging.PubMaster, n_points: int, d_rel: float, valid: bool = True) -> None:
  msg = messaging.new_message('radarTracks')
  msg.valid = valid   # 生产端 card.py 在雷达错误时置 False（envelope，见 sm.valid）
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
  # monkeypatch 打的是策略表：radard 若没走 stream_gate，这个 patch 不生效，测试即红
  import openpilot.common.stream_gate as stream_gate
  monkeypatch.setitem(stream_gate.MAX_AGE_S, "radarTracks", 0.2)
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(10):
      _publish_tracks(publisher, 1, 10.0)
      time.sleep(0.05)
    assert cache.snapshot()["stale"] is False
    time.sleep(0.4)  # 静默超过调低后的登记阈值
    assert cache.snapshot()["stale"] is True
  finally:
    exit_event.set()
    t.join(timeout=2)


def test_invalid_envelope_frame_goes_stale(publisher):
  """envelope invalid（card.py 报雷达错误时）的帧必须标 stale，不能当新鲜（③）。"""
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):
      _publish_tracks(publisher, 1, 10.0)
      time.sleep(0.05)
      if cache.snapshot().get("stale") is False:
        break
    assert cache.snapshot()["stale"] is False
    for _ in range(10):
      _publish_tracks(publisher, 1, 10.0, valid=False)
      time.sleep(0.05)
      if cache.snapshot().get("stale") is True:
        break
    assert cache.snapshot()["stale"] is True
  finally:
    exit_event.set()
    t.join(timeout=2)
