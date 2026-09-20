# system/lanlinkd/cruisebuttonsd.py
"""cruiseButtonsDebug 快照线程:照 AvoidanceCache 模式,缓存最新一帧巡航按钮
控制数据,线程安全。20Hz 在线观测流(不落盘),给 lanlink 巡航设置页的状态
卡片看 —— STALE 1s(20 帧没新数据即视为停更)。
"""
import threading
import time

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog

STALE_AFTER_MS = 1000


class CruiseButtonsCache:
  def __init__(self):
    self._lock = threading.Lock()
    self._snapshot: dict = {"stale": True}
    self._recv_ms: float = 0.0

  def run(self, exit_event: threading.Event) -> None:
    try:
      sm = messaging.SubMaster(['cruiseButtonsDebug'])
    except Exception:
      cloudlog.exception("lanlink cruisebuttonsd: SubMaster init failed")
      return
    while not exit_event.is_set():
      sm.update(1000)
      if not sm.updated['cruiseButtonsDebug']:
        continue
      dbg = sm['cruiseButtonsDebug']
      try:
        snap = {
          "stale": False,
          "logMonoTime": int(sm.logMonoTime['cruiseButtonsDebug']),
          "enabled": bool(dbg.enabled),
          "btConnected": bool(dbg.btConnected),
          "btState": int(dbg.btState),
          "ceilingKph": float(dbg.ceilingKph),
          "targetKph": float(dbg.targetKph),
          "setSpeedKph": float(dbg.setSpeedKph),
          "vEgoKph": float(dbg.vEgoKph),
          "quantumKph": float(dbg.quantumKph),
          "sccActive": bool(dbg.sccActive),
          "leadPresent": bool(dbg.leadPresent),
          "leadSpeedKph": float(dbg.leadSpeedKph),
          "standstill": bool(dbg.standstill),
          "lastButton": int(dbg.lastButton),
          "lastEchoOurs": bool(dbg.lastEchoOurs),
          "unexplained": int(dbg.unexplained),
          "frozen": bool(dbg.frozen),
        }
      except Exception:
        cloudlog.exception("lanlink cruisebuttonsd: snapshot build failed")
        continue
      with self._lock:
        self._snapshot = snap
        self._recv_ms = time.monotonic() * 1000.0

  def snapshot(self) -> dict:
    with self._lock:
      snap = dict(self._snapshot)
      recv_ms = self._recv_ms
    if not snap.get("stale") and (time.monotonic() * 1000.0 - recv_ms) > STALE_AFTER_MS:
      snap = {"stale": True}
    return snap
