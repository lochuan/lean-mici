# system/lanlinkd/radard.py
"""radarTracks 快照线程：缓存最新一帧 ACC 雷达点迹，线程安全。

与 statusd.py 的 StatusCache 同款模式：独立线程跑 SubMaster，锁下缓存
最新快照。区别在于雷达点是 20Hz 高频数据，不值得（也不需要）每帧做
快照构建——只在新报文到达时原样拷贝 points，序列化推迟到 REST handler。

staleness 用本地接收时刻（time.monotonic）判断，不比对 logMonoTime：
card 与本进程同机同源，但直接比报文时间戳会把 SubMaster 积压的旧帧
误判为新鲜，本地计时更直白。
"""
import threading
import time

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.common.stream_gate import StreamStatus, stream_status

# radarTracks 新鲜度走 ``common.stream_gate``（登记阈值 2s，理由随表登记）：
# 20Hz 点迹、card 熄火即停报（熄火、card 未运行、无雷达平台都会落到这里）。
# staleness 用本地接收时刻（time.monotonic）判断，不比对 logMonoTime：
# card 与本进程同机同源，但直接比报文时间戳会把 SubMaster 积压的旧帧
# 误判为新鲜，本地计时更直白。


def _point(p) -> dict:
  return {
    "trackId": int(p.trackId),
    "dRel": float(p.dRel),
    "yRel": float(p.yRel),
    "vRel": float(p.vRel),
  }


class RadarCache:
  def __init__(self):
    self._lock = threading.Lock()
    self._snapshot: dict = {"stale": True}
    self._recv_ms: float = 0.0
    self._recv_valid: bool = False

  def run(self, exit_event: threading.Event) -> None:
    try:
      sm = messaging.SubMaster(['radarTracks'])
    except Exception:
      cloudlog.exception("lanlink radard: SubMaster init failed")
      return
    while not exit_event.is_set():
      sm.update(1000)
      if not sm.updated['radarTracks']:
        continue
      rd = sm['radarTracks']
      try:
        snap = {
          "stale": False,
          "logMonoTime": int(sm.logMonoTime['radarTracks']),
          "points": [_point(p) for p in rd.points],
          "errors": {
            "canError": bool(rd.errors.canError),
            "radarUnavailableTemporary": bool(rd.errors.radarUnavailableTemporary),
          },
        }
      except Exception:
        cloudlog.exception("lanlink radard: snapshot build failed")
        continue
      with self._lock:
        self._snapshot = snap
        self._recv_ms = time.monotonic() * 1000.0
        self._recv_valid = bool(sm.valid['radarTracks'])  # ③：无效帧不过接收门

  def snapshot(self) -> dict:
    with self._lock:
      snap = dict(self._snapshot)
      recv_ms = self._recv_ms
      recv_valid = self._recv_valid
    age_s = None if recv_ms == 0.0 else (time.monotonic() * 1000.0 - recv_ms) / 1000.0
    if stream_status("radarTracks", age_s, valid=recv_valid) is not StreamStatus.FRESH:
      return {"stale": True}
    return snap
