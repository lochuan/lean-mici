# system/lanlinkd/statusd.py
"""SubMaster 快照线程：1Hz 采集，线程安全缓存。"""
import threading

from openpilot.cereal import messaging

from openpilot.system.lanlinkd.status_snapshot import build_capabilities, build_snapshot

SERVICES = ['deviceState', 'carState', 'pandaStates', 'gpsLocation', 'carParams']


class StatusCache:
  def __init__(self, version_info: dict, device_type: str):
    self._version_info = version_info
    self._device_type = device_type
    self._lock = threading.Lock()
    self._snapshot: dict = {"stale": True}
    self._capabilities: dict = {}

  def run(self, exit_event: threading.Event) -> None:
    sm = messaging.SubMaster(SERVICES)
    cp = None
    while not exit_event.is_set():
      sm.update(1000)
      if sm.updated['carParams']:
        cp = sm['carParams']
      # 首轮必构建（否则空 dict 永锁默认值）；carParams 更新时重建
      caps = build_capabilities(cp, None, self._device_type) \
        if (sm.updated['carParams'] or not self._capabilities) else self._capabilities
      snap = build_snapshot({name: sm[name] for name in SERVICES}, self._version_info, caps)
      with self._lock:
        self._snapshot = snap
        self._capabilities = caps

  def snapshot(self) -> dict:
    with self._lock:
      return dict(self._snapshot)

  def capabilities(self) -> dict:
    with self._lock:
      return dict(self._capabilities)
