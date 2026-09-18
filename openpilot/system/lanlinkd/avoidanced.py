# system/lanlinkd/avoidanced.py
"""avoidanceDebug 快照线程：缓存最新一帧避让监测数据，线程安全。

独立线程跑 SubMaster，锁下缓存最新快照。avoidanceDebug 是 5Hz 在线观测
流（不落盘），给 lanlink 鸟瞰图和标定工具看——每帧构建 targets 列表
dict，序列化在收帧时一次完成。快照含雷达 CAN 错误标志（canError /
radarUnavailable，来自 radarTracks.errors，随 debug 透传），所以前端
不再需要独立的 /api/radar 端点。

staleness 用本地接收时刻（time.monotonic）判断：
avoidanceDebug 是 5Hz，STALE_AFTER_MS 取 1s（5 帧没新数据即视为停更）。
"""
import threading
import time

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog

STALE_AFTER_MS = 1000


def _target(t) -> dict:
  return {
    "dRel": float(t.dRel),
    "yRel": float(t.yRel),
    "vRel": float(t.vRel),
    "cls": str(t.cls),
    "conf": float(t.conf),
    "weight": float(t.weight),
    "matched": bool(t.matched),
    "inGate": bool(t.inGate),
    "vision": bool(t.vision),
    "pairId": int(t.pairId),
  }


class AvoidanceCache:
  def __init__(self):
    self._lock = threading.Lock()
    self._snapshot: dict = {"stale": True}
    self._recv_ms: float = 0.0

  def run(self, exit_event: threading.Event) -> None:
    try:
      sm = messaging.SubMaster(['avoidanceDebug'])
    except Exception:
      cloudlog.exception("lanlink avoidanced: SubMaster init failed")
      return
    while not exit_event.is_set():
      sm.update(1000)
      if not sm.updated['avoidanceDebug']:
        continue
      dbg = sm['avoidanceDebug']
      try:
        snap = {
          "stale": False,
          "logMonoTime": int(sm.logMonoTime['avoidanceDebug']),
          "valid": bool(dbg.valid),
          "active": bool(dbg.active),
          "direction": int(dbg.direction),
          "yDes": float(dbg.yDes),
          "bias": float(dbg.bias),
          "maxOffset": float(dbg.maxOffset),
          "bsmLeft": bool(dbg.bsmLeft),
          "bsmRight": bool(dbg.bsmRight),
          "vEgo": float(dbg.vEgo),
          "nRadar": int(dbg.nRadar),
          "nVision": int(dbg.nVision),
          "nAssociated": int(dbg.nAssociated),
          "edgeClearance": float(dbg.edgeClearance),
          "canError": bool(dbg.canError),
          "radarUnavailable": bool(dbg.radarUnavailable),
          "targets": [_target(t) for t in dbg.targets],
        }
      except Exception:
        cloudlog.exception("lanlink avoidanced: snapshot build failed")
        continue
      with self._lock:
        self._snapshot = snap
        self._recv_ms = time.monotonic() * 1000.0

  def snapshot(self) -> dict:
    with self._lock:
      snap = dict(self._snapshot)
      recv_ms = self._recv_ms
    if not snap.get("stale") and (time.monotonic() * 1000.0 - recv_ms) > STALE_AFTER_MS:
      return {"stale": True}
    return snap
