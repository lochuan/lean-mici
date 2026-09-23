# system/lanlinkd/calibration.py
"""在线标定会话控制：后台收集 avoidanceDebug 配对目标，停止时拟合。

复用 eagled.calibrate 的纯函数层（extract_pairs / fit_calibrated_offsets /
format_constants_block），本模块只负责会话生命周期：start 起线程订阅
avoidanceDebug 持续收集配对，stop 停止并拟合，结果（含可粘贴常量块）存
last_result 供前端展示。与 CLI 版（python -m ...calibrate --duration N）的
区别：无固定时长，开/停由 lanlink 按钮控制；其余语义一致（增量拟合、
p95<0.3m pass、侧向截距只警告）。

线程安全：所有状态在锁下读写；start 幂等拒绝（已在跑返回 False）。
"""
import threading
import time

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.eagled.calibrate import (
  MIN_FIT_PAIRS,
  CalibPair,
  extract_pairs,
  fit_calibrated_offsets,
  format_constants_block,
)

# 与 CLI 版一致的推荐下限：低于此值仍可拟合（>=MIN_FIT_PAIRS）但结果标记
# 不可信，前端要如实显示
RECOMMENDED_MIN_PAIRS = 30


class CalibrationController:
  def __init__(self, sm_factory=None):
    self._lock = threading.Lock()
    self._sm_factory = sm_factory  # lazy: device 上才 import cereal
    self._thread: threading.Thread | None = None
    self._stop_event = threading.Event()
    self._pairs: list[CalibPair] = []
    self._start_t: float = 0.0
    self.last_result: dict | None = None
    self.last_error: str | None = None

  # ---- 会话生命周期 ----

  def start(self) -> bool:
    """Start a collection session. False if one is already running."""
    with self._lock:
      if self._thread is not None and self._thread.is_alive():
        return False
      self._stop_event = threading.Event()
      self._pairs = []
      self._start_t = time.monotonic()
      self.last_error = None
      self._thread = threading.Thread(target=self._run, args=(self._stop_event,),
                                       name="lanlink_calibration", daemon=True)
      self._thread.start()
      return True

  def stop(self) -> dict:
    """Stop the session and fit. Returns the status dict (see :meth:`status`)."""
    with self._lock:
      running = self._thread is not None and self._thread.is_alive()
      pairs = list(self._pairs)
    if running:
      self._stop_event.set()
      self._thread.join(timeout=5.0)
    self._fit(pairs)
    return self.status()

  def status(self) -> dict:
    with self._lock:
      running = self._thread is not None and self._thread.is_alive()
      return {
        "running": running,
        "n_pairs": len(self._pairs),
        "elapsed_s": max(0.0, time.monotonic() - self._start_t) if running or self._pairs else 0.0,
        "last_error": self.last_error,
        "last_result": self.last_result,
      }

  # ---- 内部 ----

  def _run(self, stop_event: threading.Event) -> None:
    try:
      from openpilot.cereal import messaging
      sm = messaging.SubMaster(['avoidanceDebug'])
    except Exception:
      cloudlog.exception("lanlink calibration: SubMaster init failed")
      with self._lock:
        self.last_error = "SubMaster init failed (is openpilot running?)"
      return
    while not stop_event.is_set():
      sm.update(500)
      if sm.updated.get("avoidanceDebug"):
        dbg = sm["avoidanceDebug"]
        with self._lock:
          self._pairs.extend(extract_pairs(dbg.targets, float(dbg.vEgo)))

  def _fit(self, pairs: list[CalibPair]) -> None:
    with self._lock:
      self._pairs = pairs  # keep final count even after stop
    if len(pairs) < MIN_FIT_PAIRS:
      with self._lock:
        self.last_error = (f"配对不足：{len(pairs)} 对（最少 {MIN_FIT_PAIRS}）。"
                           + "开避让跟车行驶，前方要有其他车辆，再试。")
        self.last_result = None
      return
    try:
      result = fit_calibrated_offsets(pairs)
    except Exception:
      cloudlog.exception("lanlink calibration: fit failed")
      with self._lock:
        self.last_error = "拟合失败（见 swaglog）"
        self.last_result = None
      return
    result["constants_block"] = format_constants_block(result)
    result["insufficient"] = len(pairs) < RECOMMENDED_MIN_PAIRS
    with self._lock:
      self.last_result = result
