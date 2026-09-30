"""Utilities for reading real time clocks and keeping soft real time constraints."""
import gc
import os
import sys
import time

from setproctitle import getproctitle

from openpilot.common.utils import MovingAverage
from openpilot.common.hardware import PC


# time step for each process
DT_CTRL = 0.01  # controlsd
DT_MDL = 0.05  # model
DT_HW = 0.5  # hardwared and manager
DT_DMON = 0.05  # driver monitoring


class Priority:
  # CORE 2
  # - modeld = 55
  # - camerad = 54
  CTRL_LOW = 51 # plannerd & radard

  # CORE 3
  # - pandad = 55
  CTRL_HIGH = 53


def drop_realtime() -> None:
  if sys.platform == 'linux' and not PC:
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))


def set_core_affinity(cores: list[int]) -> None:
  if sys.platform == 'linux' and not PC:
    os.sched_setaffinity(0, cores)


def config_realtime_process(cores: int | list[int], priority: int) -> None:
  gc.disable()
  if sys.platform == 'linux' and not PC:
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(priority))
  c = cores if isinstance(cores, list) else [cores, ]
  set_core_affinity(c)


def _pin_all_threads(cores: list[int], task_dir: str = "/proc/self/task") -> None:
  """把进程内**所有**线程钉到 ``cores``。

  set_core_affinity 只钉调用线程 —— numpy/OpenBLAS 的线程池在 import 期就带着
  全核掩码存在(2026-09-25 设备实测:7 条线程漏在 0-7,含 core 1),把
  "core 1 不可触碰"的红线穿透了。遍历 /proc/self/task 一次钉齐;之后新建的
  线程继承被钉后的创建者掩码,不会再漏。线程在 listdir 与 pin 之间退出是
  正常竞态,OSError 忽略。非 Linux(开发机)无 /proc,安静跳过。
  """
  try:
    tids = sorted(int(t) for t in os.listdir(task_dir))
  except (OSError, ValueError):
    return
  for tid in tids:
    try:
      os.sched_setaffinity(tid, cores)
    except OSError:
      pass


def config_best_effort_process(cores: int | list[int]) -> None:
  """Non-RT (SCHED_OTHER) process pinned to ``cores``.

  For daemons that must never preempt latency-critical producers. The canonical
  case: sensord (FIFO 1, core 1) timestamps IMU samples from the GPIO IRQ and
  locationd rejects any sample whose publish latency exceeds 100ms — an RT
  burst anywhere on core 1 directly converts into locationdTemporaryError and
  a refused engagement.
  """
  gc.disable()
  drop_realtime()
  c = cores if isinstance(cores, list) else [cores, ]
  set_core_affinity(c)
  if sys.platform == 'linux' and not PC:
    _pin_all_threads(c)


class Ratekeeper:
  def __init__(self, rate: float, print_delay_threshold: float | None = 0.0) -> None:
    """Rate in Hz for ratekeeping. print_delay_threshold must be nonnegative."""
    self._interval = 1. / rate
    self._print_delay_threshold = print_delay_threshold
    self._frame = 0
    self._remaining = 0.0
    self._process_name = getproctitle()
    self._last_monitor_time = -1.
    self._next_frame_time = -1.

    self.avg_dt = MovingAverage(100)
    self.avg_dt.add_value(self._interval)

  @property
  def frame(self) -> int:
    return self._frame

  @property
  def remaining(self) -> float:
    return self._remaining

  @property
  def lagging(self) -> bool:
    expected_dt = self._interval * (1 / 0.9)
    return self.avg_dt.get_average() > expected_dt

  # Maintain loop rate by calling this at the end of each loop
  def keep_time(self) -> bool:
    lagged = self.monitor_time()
    if self._remaining > 0:
      time.sleep(self._remaining)
    return lagged

  # Monitors the cumulative lag, but does not enforce a rate
  def monitor_time(self) -> bool:
    if self._last_monitor_time < 0:
      self._next_frame_time = time.monotonic() + self._interval
      self._last_monitor_time = time.monotonic()

    prev = self._last_monitor_time
    self._last_monitor_time = time.monotonic()
    self.avg_dt.add_value(self._last_monitor_time - prev)

    lagged = False
    remaining = self._next_frame_time - time.monotonic()
    self._next_frame_time += self._interval
    if self._print_delay_threshold is not None and remaining < -self._print_delay_threshold:
      print(f"{self._process_name} lagging by {-remaining * 1000:.2f} ms")
      lagged = True
    self._frame += 1
    self._remaining = remaining
    return lagged
