# system/lanlinkd/calibration.py
"""在线标定会话控制：后台收集 eagleDebug 配对目标，停止时拟合，一键保存生效。

复用 eagled.calibrate 的纯函数层（extract_pairs / fit_calibrated_offsets /
propose_camera_to_front），本模块只负责会话生命周期：start 起线程订阅
eagleDebug 持续收集配对，stop 停止并拟合（结果含建议值与防呆结论，存
last_result 供前端展示），apply 把建议值经 model_geometry 唯一写点写进
Params ``CameraToFront``，消费方下一帧生效（票 #7）。与 CLI 版（python -m
...calibrate --duration N）的区别：无固定时长，开/停/保存由 lanlink 按钮控制；
其余语义一致（增量拟合、分档判据、侧向截距只警告、同一保存防呆）。

线程安全：所有状态在锁下读写；start 幂等拒绝（已在跑返回 False）。
"""
import threading
import time

from openpilot.common.model_geometry import read_camera_to_front, write_camera_to_front
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.eagled.calibrate import (
  MIN_FIT_PAIRS,
  MIN_SAVE_PAIRS,
  CalibPair,
  extract_pairs,
  fit_calibrated_offsets,
  propose_camera_to_front,
)


class CalibrationController:
  def __init__(self, params, sm_factory=None):
    self._lock = threading.Lock()
    self._params = params
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

  def apply(self) -> tuple[bool, dict | str]:
    """「保存并生效」：写入上次拟合的建议值。(True, status) 或 (False, 拒绝原因)。

    写的是拟合时算好的绝对建议值，连点多次结果相同，不会重复累加增量。
    """
    with self._lock:
      result = self.last_result
    if result is None:
      return False, "没有可保存的精修结果，先完成一轮标定"
    proposal = result["camera_to_front"]
    if not proposal["savable"]:
      return False, proposal["reject_reason"]
    try:
      write_camera_to_front(self._params, proposal["proposed_m"])
    except Exception:
      cloudlog.exception("lanlink calibration: saving CameraToFront failed")
      return False, "保存失败（见 swaglog）"
    with self._lock:
      result["saved"] = True
    return True, self.status()

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
      sm = messaging.SubMaster(['eagleDebug'])
    except Exception:
      cloudlog.exception("lanlink calibration: SubMaster init failed")
      with self._lock:
        self.last_error = "SubMaster init failed (is openpilot running?)"
      return
    while not stop_event.is_set():
      sm.update(500)
      if sm.updated.get("eagleDebug"):
        dbg = sm["eagleDebug"]
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
    result["camera_to_front"] = propose_camera_to_front(result, read_camera_to_front(self._params))
    result["insufficient"] = len(pairs) < MIN_SAVE_PAIRS
    result["saved"] = False
    with self._lock:
      self.last_result = result
