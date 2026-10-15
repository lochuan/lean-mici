# system/lanlinkd/statusd.py
"""SubMaster 快照线程：1Hz 采集，线程安全缓存。capabilities 从持久化 params 生成（停车可用）。"""
import threading
import time
from collections import deque

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog

from openpilot.system.lanlinkd.diagnostics import PARAM_KEYS as DIAGNOSTICS_PARAM_KEYS, Diagnostics
from openpilot.system.lanlinkd.status_snapshot import (
  build_capabilities, build_model_status, build_snapshot, build_torque_status, offline_torque_from_cp,
  timing_frame, torque_params_dict, torque_params_from_cache)

# carParams 移除（capabilities 不再依赖实时 CP）
SERVICES = ['deviceState', 'carState', 'pandaStates', 'gpsLocation', 'modelV2', 'modelDataV2SP', 'lateralTorqueParameters',
            # 诊断页（diagnostics.py）
            'controlsState', 'carControl', 'vehicleParameters', 'extrinsicsCalibration', 'lateralDelay']
MODEL_FRAMES = 50  # 最近 50 帧（20Hz ≈ 2.5s）的模型来源
TIMING_FRAMES = 100  # 分段耗时/小模型原因窗口（≈5s）

_CAP_PARAM_KEYS = (
  "IsReleaseSpBranch", "IsDevelopmentBranch", "ToyotaEnforceStockLongitudinal",
  "AlphaLongitudinalEnabled", "IntelligentCruiseButtonManagement")
_TORQUE_TOGGLE_KEYS = ("EnforceTorqueControl", "LiveTorqueParamsToggle", "SpeedDependentTorqueToggle")
TORQUE_LIVE_TIMEOUT = 5.0  # s；torqued 4Hz，超过即视为未运行（停车），改读落盘缓存


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
    self._cap_inputs: tuple | None = None
    self._model_frames: deque[int] = deque(maxlen=MODEL_FRAMES)
    self._timing_frames: deque[tuple] = deque(maxlen=TIMING_FRAMES)
    self._torque_live: dict | None = None
    self._torque_live_t = 0.0
    self._torque_cache_bytes = None
    self._torque_cache: dict | None = None
    self._offline_cp_bytes = None
    self._offline_torque: dict = offline_torque_from_cp(None)
    self._diag = Diagnostics()
    self._diag_error_logged = False

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

  def _torque_status(self) -> dict:
    """行驶中用 torqued 实时消息；停车（torqued 不跑）用上次落盘的 LiveTorqueParameters。解析结果按 bytes 缓存。"""
    p = self._params
    cp_bytes = p.get("CarParamsPersistent")
    if cp_bytes != self._offline_cp_bytes:
      self._offline_cp_bytes = cp_bytes
      self._offline_torque = offline_torque_from_cp(cp_bytes)
    toggles = {k: bool(p.get_bool(k)) for k in _TORQUE_TOGGLE_KEYS}
    if self._torque_live is not None and time.monotonic() - self._torque_live_t < TORQUE_LIVE_TIMEOUT:
      return build_torque_status(self._torque_live, "live", self._offline_torque, toggles)
    cache_bytes = p.get("LiveTorqueParameters")
    if cache_bytes != self._torque_cache_bytes:
      self._torque_cache_bytes = cache_bytes
      self._torque_cache = torque_params_from_cache(cache_bytes)
    return build_torque_status(self._torque_cache, "cache", self._offline_torque, toggles)

  def run(self, exit_event: threading.Event) -> None:
    try:
      sm = messaging.SubMaster(SERVICES)
    except Exception:
      cloudlog.exception("lanlink statusd: SubMaster init failed")
      return
    while not exit_event.is_set():
      sm.update(1000)
      if sm.updated['modelV2']:
        self._model_frames.append(int(bool(sm['modelV2'].big)))
      if sm.updated['modelDataV2SP']:
        self._timing_frames.append(timing_frame(sm['modelDataV2SP']))
      self._update_diagnostics(sm)
      if sm.updated['lateralTorqueParameters']:
        self._torque_live = torque_params_dict(sm['lateralTorqueParameters'])
        self._torque_live_t = time.monotonic()
      try:
        caps = self._capabilities_from_params()
        snap = build_snapshot({name: sm[name] for name in SERVICES}, self._version_info, caps)
        snap["model"] = build_model_status(
          self._model_frames, bool(self._params.get_bool("BigmodelToggle")),
          str(self._params.get("BigmodelLinkState") or ""), self._timing_frames)
        snap["torque"] = self._torque_status()
      except Exception:
        cloudlog.exception("lanlink statusd: snapshot build failed")
        continue
      with self._lock:
        self._snapshot = snap

  def _update_diagnostics(self, sm) -> None:
    """诊断统计出错只记一次日志，不能拖垮状态快照。锁：report() 在 HTTP 线程读同一份累计量。"""
    with self._lock:
      try:
        self._diag.update(sm, time.monotonic())
      except Exception:
        if not self._diag_error_logged:
          cloudlog.exception("lanlink statusd: diagnostics update failed")
          self._diag_error_logged = True

  def diagnostics(self) -> dict:
    # param 读盘放在锁外：锁内只做统计汇总，不阻塞状态循环
    param_values = {k: self._params.get(k) for k in DIAGNOSTICS_PARAM_KEYS}
    with self._lock:
      learned = (self._snapshot.get("torque") or {}).get("learned")
      return self._diag.report(param_values, learned, time.monotonic())

  def snapshot(self) -> dict:
    with self._lock:
      return dict(self._snapshot)

  def capabilities(self) -> dict:
    with self._lock:
      return dict(self._capabilities)
