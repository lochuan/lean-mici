# system/lanlinkd/statusd.py
"""SubMaster 快照线程：1Hz 采集，线程安全缓存。capabilities 从持久化 params 生成（停车可用）。"""
import threading

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog

from openpilot.system.lanlinkd.status_snapshot import (
  build_capabilities, build_models_download, build_snapshot)

# carParams 移除（capabilities 不再依赖实时 CP）；modelManagerSP 新增（模型下载进度）
SERVICES = ['deviceState', 'carState', 'pandaStates', 'gpsLocation', 'modelManagerSP']

_CAP_PARAM_KEYS = (
  "IsReleaseSpBranch", "IsDevelopmentBranch", "ToyotaEnforceStockLongitudinal",
  "AlphaLongitudinalEnabled", "IntelligentCruiseButtonManagement")


def _bundle_or_none(v):
  # bundle param 为 JSON dict 或 None；作为缓存输入元组参与相等比较
  return v if isinstance(v, dict) else None


class StatusCache:
  def __init__(self, version_info: dict, device_type: str, params=None):
    self._version_info = version_info
    self._device_type = device_type
    self._params = params
    self._lock = threading.Lock()
    self._snapshot: dict = {"stale": True}
    self._capabilities: dict = {}
    self._download: dict | None = None
    self._cap_inputs: tuple | None = None

  def _capabilities_from_params(self) -> dict:
    """输入（CP/CPSP bytes、bundle、相关 bool params）不变则复用上次结果。"""
    p = self._params
    inputs = tuple()
    if p is not None:
      inputs = (p.get("CarParamsPersistent"), p.get("CarParamsSPPersistent"),
                _bundle_or_none(p.get("CarPlatformBundle")),
                *(p.get_bool(k) for k in _CAP_PARAM_KEYS))
    if not self._capabilities or inputs != self._cap_inputs:
      self._cap_inputs = inputs
      self._capabilities = build_capabilities(p, self._device_type)
    return self._capabilities

  def run(self, exit_event: threading.Event) -> None:
    try:
      sm = messaging.SubMaster(SERVICES)
    except Exception:
      cloudlog.exception("lanlink statusd: SubMaster init failed")
      return
    while not exit_event.is_set():
      sm.update(1000)
      try:
        caps = self._capabilities_from_params()
        download = build_models_download(sm["modelManagerSP"])
        snap = build_snapshot({name: sm[name] for name in SERVICES}, self._version_info, caps)
      except Exception:
        cloudlog.exception("lanlink statusd: snapshot build failed")
        continue
      with self._lock:
        self._snapshot = snap
        self._download = download

  def snapshot(self) -> dict:
    with self._lock:
      return dict(self._snapshot)

  def capabilities(self) -> dict:
    with self._lock:
      return dict(self._capabilities)

  def download(self) -> dict | None:
    with self._lock:
      return dict(self._download) if self._download else None
