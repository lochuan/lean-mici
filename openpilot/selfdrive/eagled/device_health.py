"""设备遥测节流（DeviceHealth）for eagled's vision chain.

这是「健康度」三个易混概念之一（见 common.stream_gate 模块 docstring 的
拆词）：按 CPU/内存遥测调推理节流，与消息新鲜度、雷达健康字段均无关。

Ported from StarPilot (starpilot/common/cpu_throttle.py and the memory-pressure
/ livePose-recovery intervals in starpilot/system/speed_limit_vision.py): a
several-hundred-ms detector pass on Mici must never be scheduled back-to-back
while the rest of the device is already saturated. eagled's own 09-24 incident
(RT bursts starved sensord into locationdTemporaryError) is the same failure
mode seen from the other side.
"""

from __future__ import annotations

from functools import lru_cache
import math
import time
from pathlib import Path

from openpilot.common.swaglog import cloudlog

DEVICE_BUSY_AVG_CPU_USAGE_PERCENT = 74.0
DEVICE_BUSY_MAX_CPU_USAGE_PERCENT = 89.0
DEVICE_BUSY_HOT_CORE_COUNT = 4

# Next interval >= last inference duration * ratio: guarantee the device gets
# the remainder of the budget. 2.5x turns an 85ms pass into ~213ms cadence.
PROCESSING_THROTTLE_RATIO = 2.5
PROCESSING_THROTTLE_MAX_INTERVAL = 1.5

MEMORY_PRESSURE_AVAILABLE_KB = 512 * 1024
MEMORY_CRITICAL_AVAILABLE_KB = 256 * 1024
MEMORY_RECOVERY_AVAILABLE_KB = 768 * 1024
MEMORY_PRESSURE_USAGE_PERCENT = 88.0
MEMORY_CRITICAL_USAGE_PERCENT = 94.0
MEMORY_RECOVERY_USAGE_PERCENT = 82.0
MEMORY_PRESSURE_INFERENCE_INTERVAL = 2.0

# locationd inputsOK false (the same signal selfdrived gates engagement on):
# back off to ~1Hz for a window after it was last seen unhealthy.
LIVE_POSE_RECOVERY_THROTTLE_SECONDS = 2.0
LIVE_POSE_RECOVERY_INFERENCE_INTERVAL = 1.0

_throttle_state: dict[str, dict[str, float]] = {}


@lru_cache(maxsize=1)
def _online_cpu_count() -> int | None:
  try:
    spec = Path("/sys/devices/system/cpu/online").read_text(encoding="utf-8").strip()
    count = 0
    for group in spec.split(","):
      bounds = group.split("-", maxsplit=1)
      first = int(bounds[0])
      last = int(bounds[-1])
      if last < first:
        return None
      count += last - first + 1
    return count or None
  except (OSError, ValueError):
    return None


def _online_cpu_usage(cpu_usage, cores=None) -> list[float]:
  usage = [float(value) for value in cpu_usage]
  online_count = _online_cpu_count()
  if online_count is not None and online_count < len(usage):
    usage = usage[:online_count]

  if cores is not None:
    usage = [usage[core] for core in cores if 0 <= core < len(usage)]
  return usage


def _compute_throttle_factor(average: float, hot_cores: int) -> float:
  average_factor = 1.0 if average < DEVICE_BUSY_AVG_CPU_USAGE_PERCENT \
    else 1.0 + (average - DEVICE_BUSY_AVG_CPU_USAGE_PERCENT) / 8.0
  hot_factor = 1.0 + max(0, hot_cores - DEVICE_BUSY_HOT_CORE_COUNT + 1) * 0.5
  return min(max(average_factor, hot_factor), 4.0)


def device_cpu_throttle_factor(cpu_usage, name: str = "eagled", cores=None) -> float:
  """Low-pass-filtered CPU throttle factor (>=1.0), from StarPilot."""
  usage = _online_cpu_usage(cpu_usage, cores=cores)
  if not usage:
    return 1.0

  now = time.monotonic()
  state = _throttle_state.setdefault(name, {"factor": 1.0, "last_time": now})
  dt = max(0.0, now - state["last_time"])
  state["last_time"] = now

  average = sum(usage) / len(usage)
  hot_cores = sum(core >= DEVICE_BUSY_MAX_CPU_USAGE_PERCENT for core in usage)
  target = _compute_throttle_factor(average, hot_cores)

  alpha = min(1.0 - math.exp(-0.8 * dt), 1.0)
  state["factor"] = min(max(target * alpha + state["factor"] * (1.0 - alpha), 1.0), 4.0)
  return state["factor"]


def memory_pressure_level(available_kb: float | None, usage_percent: float | None) -> str:
  """Classify memory pressure without treating low free page cache as an emergency."""
  critical = (
    available_kb is not None and available_kb <= MEMORY_CRITICAL_AVAILABLE_KB
  ) or (
    usage_percent is not None and usage_percent >= MEMORY_CRITICAL_USAGE_PERCENT
  )
  if critical:
    return "critical"

  pressure = (
    available_kb is not None and available_kb <= MEMORY_PRESSURE_AVAILABLE_KB
  ) or (
    usage_percent is not None and usage_percent >= MEMORY_PRESSURE_USAGE_PERCENT
  )
  return "pressure" if pressure else "normal"


class DeviceHealth:
  """Aggregates deviceState/procLog/deviceMotion into a vision cadence.

  All reads are defensive: a missing/invalid service degrades to the healthy
  default so the daemon never blocks on telemetry it doesn't have yet.
  """

  def __init__(self, cores=None):
    self.cores = cores
    self.memory_state = "normal"
    self.cpu_usage: list[float] = []
    self.inputs_ok = True
    self.last_inputs_not_ok_t = -float("inf")
    self._last_reason = ""

  def refresh(self, sm) -> None:
    try:
      if sm.valid.get("deviceState", False):
        self.cpu_usage = [float(v) for v in sm["deviceState"].cpuUsagePercent]
      else:
        self.cpu_usage = []
    except Exception:
      self.cpu_usage = []

    try:
      usage_percent = float(sm["deviceState"].memoryUsagePercent) if sm.valid.get("deviceState", False) else None
    except Exception:
      usage_percent = None
    available_kb = None
    try:
      if sm.valid.get("procLog", False):
        # our cereal stores bytes (3.5GB total); StarPilot's field was already KB
        available_kb = int(sm["procLog"].mem.available) / 1024.0
    except Exception:
      available_kb = None

    try:
      inputs_ok = bool(sm["deviceMotion"].inputsOK)
      if not inputs_ok:
        self.last_inputs_not_ok_t = time.monotonic()
      self.inputs_ok = inputs_ok
    except Exception:
      self.inputs_ok = True

    requested = memory_pressure_level(available_kb, usage_percent)
    # hysteresis: reclaim fluctuations must not flap inference on/off (StarPilot)
    if self.memory_state == "critical":
      recovered = (available_kb is not None and available_kb >= MEMORY_RECOVERY_AVAILABLE_KB and
                   (usage_percent is None or usage_percent <= MEMORY_RECOVERY_USAGE_PERCENT)) or \
                  (available_kb is None and usage_percent is not None and usage_percent <= MEMORY_RECOVERY_USAGE_PERCENT)
      self.memory_state = "normal" if recovered else ("pressure" if requested != "critical" else "critical")
    elif self.memory_state == "pressure":
      if requested == "critical":
        self.memory_state = "critical"
      elif (available_kb is not None and available_kb < MEMORY_RECOVERY_AVAILABLE_KB) or \
           (usage_percent is not None and usage_percent > MEMORY_RECOVERY_USAGE_PERCENT):
        pass
      else:
        self.memory_state = "normal"
    else:
      self.memory_state = requested

  def inference_interval(self, now: float, base_s: float, last_duration_s: float) -> tuple[float, str]:
    """Effective min interval until the next vision tick, plus the reason."""
    interval, reason = base_s, "steady"

    if self.memory_state in ("pressure", "critical"):
      interval = max(interval, MEMORY_PRESSURE_INFERENCE_INTERVAL)
      reason = f"memory_{self.memory_state}"
    else:
      processing = min(PROCESSING_THROTTLE_MAX_INTERVAL, last_duration_s * PROCESSING_THROTTLE_RATIO)
      if processing > interval:
        interval = processing
        reason = "processing_cost"
      factor = device_cpu_throttle_factor(self.cpu_usage, cores=self.cores)
      if factor > 1.05:
        interval *= factor
        reason = f"cpu_{factor:.1f}x"

    if time.monotonic() - self.last_inputs_not_ok_t <= LIVE_POSE_RECOVERY_THROTTLE_SECONDS:
      if LIVE_POSE_RECOVERY_INFERENCE_INTERVAL > interval:
        interval = LIVE_POSE_RECOVERY_INFERENCE_INTERVAL
        reason = "locationd_recovery"

    if reason != self._last_reason:
      cloudlog.warning(f"eagled: vision interval {interval:.2f}s ({reason})")
      self._last_reason = reason
    return interval, reason
