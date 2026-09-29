# 大模型 outputs[0:2066) 解析 + 逐帧择优纯逻辑（04 号 C+D）。
# 择优口径（04 号票）：优先大模型、小模型兜底、运行时性能优先（结果一到就发不等截止、
# 不重试不补发）；无感切换 = 全头交叉淡入（SourceBlender）+ action 共用同一 smooth 平滑链。
# slices = MODEL_ABI §5 原始输出布局，裁掉 hidden_state/pad（状态在推理服务器，见 research 02）。
import threading
import time
from collections import deque
from statistics import median
from typing import NamedTuple

import numpy as np

from openpilot.selfdrive.modeld.parse_model_outputs import Parser

BIG_OUTPUT_LEN = 2066

# timestamp_eof 是内核 SOF_BOOT_TS（CLOCK_BOOTTIME）；与它比较的"现在"必须同钟，
# time.monotonic 不含挂起时长，设备挂起过就整体偏移。macOS 无 BOOTTIME（同 common/timing.h）
_CLOCK_BOOTTIME = getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)


def nanos_since_boot() -> int:
  return time.clock_gettime_ns(_CLOCK_BOOTTIME)

BIG_OUTPUT_SLICES: dict[str, slice] = {
  'lane_lines': slice(0, 528),
  'lane_lines_prob': slice(528, 536),
  'road_edges': slice(536, 800),
  'meta': slice(800, 855),
  'desire_pred': slice(855, 887),
  'pose': slice(887, 899),
  'wide_from_device_euler': slice(899, 905),
  'road_transform': slice(905, 917),
  'plan': slice(917, 1907),
  'lead': slice(1907, 2051),
  'lead_prob': slice(2051, 2054),
  'desire_state': slice(2054, 2062),
  'action': slice(2062, 2066),
}


def parse_big_outputs(raw: np.ndarray) -> dict[str, np.ndarray]:
  """raw = REPLY outputs[0:2066) f32；返回与小模型 outputs_dict 同构的解析结果。"""
  assert raw.shape == (BIG_OUTPUT_LEN,), f"bad big outputs shape {raw.shape}"
  outs = {k: raw[v][np.newaxis] for k, v in BIG_OUTPUT_SLICES.items()}
  return Parser().parse_outputs(outs)


class LatencyEstimator:
  """L̂：最近约 100 帧 L_n 的滑动中位，限幅 [15, 35] ms，初值 22（ADR-0001）。

  L_n = modeld 收帧时刻 − timestamp_eof（相机帧出图 → 收帧，遥测字段 cameraToModelMs）；
  每帧 L_n 由调用方进遥测。与 bigLatencyMs（REPLY 往返）是两个量，别混。
  """
  def __init__(self, window: int = 100, lo: float = 15., hi: float = 35., init: float = 22.):
    self._samples: deque[float] = deque(maxlen=window)
    self._lo, self._hi = lo, hi
    self.value = init

  def update(self, camera_to_model_ms: float) -> float:
    self._samples.append(camera_to_model_ms)
    self.value = float(min(max(median(self._samples), self._lo), self._hi))
    return self.value


class SourceBlender:
  """全头交叉淡入：w=1 直通大模型输出、w=0 直通小模型输出，切换沿 fade_frames 线性过渡。

  兜底帧没有当帧大模型输出，淡出腿用最近一次大模型输出（hold），保证过渡连续；
  稳定态不做任何滤波（性能优先：直通原值）。
  """
  def __init__(self, fade_frames: int = 10):  # 10 帧 = 0.5 s @20 Hz
    self._step = 1.0 / fade_frames
    self.w = 0.0
    self.big_last: dict[str, np.ndarray] | None = None

  def step(self, small: dict[str, np.ndarray], big: dict[str, np.ndarray] | None) -> dict[str, np.ndarray]:
    if big is not None:
      self.big_last = big
      self.w = min(1.0, self.w + self._step)
      leg = big
    else:
      self.w = max(0.0, self.w - self._step)
      leg = self.big_last
    if leg is None or self.w <= 0.0:
      return small
    if self.w >= 1.0 and big is not None:
      return big
    out = {}
    for k, v in small.items():
      lv = leg.get(k)
      out[k] = v * (1.0 - self.w) + lv * self.w if lv is not None and lv.shape == v.shape else v
    return out


class _Reply(NamedTuple):
  t_eof: int
  outputs: np.ndarray
  eof_to_reply_ms: float
  arrival_ns: int


class BigReplyLatch:
  """bigModelReply 后台接收闩（04 号 C-3）。

  读线程独立于 model.run()：收帧即盖真实到达时刻并缓存最新一帧（按 tEof 匹配），
  到达 − tEof = REPLY 往返，只进 bigLatencyMs 遥测、不进 L̂（L̂ 在 modeld 收帧点喂）；主线程
  wait_for 要么零等待拿到已到的 REPLY，要么等 Condition 到 deadline。链路 alive_s
  内没回音就只捡不等，保 20 Hz 不塌（不 modeldLagging）；一有回音自恢复。
  ponytail: 只缓存最新一帧（msgq 语义，不重试不补发），迟到旧帧不参与匹配。
  """
  def __init__(self, sm, alive_s: float = 2.0):
    self._sm = sm
    self.alive_s = alive_s
    self._cv = threading.Condition()
    self._last: _Reply | None = None
    self._last_seen = 0.0
    threading.Thread(target=self._run, daemon=True).start()

  def _on_reply(self, t_eof: int, outputs: np.ndarray, arrival_ns: int) -> None:
    eof_to_reply_ms = (arrival_ns - t_eof) / 1e6
    with self._cv:
      self._last = _Reply(t_eof, outputs, eof_to_reply_ms, arrival_ns)
      self._last_seen = time.monotonic()
      self._cv.notify_all()

  def _run(self) -> None:
    while True:
      self._sm.update(1.0)
      if not self._sm.updated['bigModelReply']:
        continue
      r = self._sm['bigModelReply']
      if len(r.outputs) < BIG_OUTPUT_LEN:  # 畸形帧别让 modeld 崩在路上
        continue
      self._on_reply(r.tEof, np.array(r.outputs[:BIG_OUTPUT_LEN], dtype=np.float32), nanos_since_boot())

  def link_alive(self) -> bool:
    return time.monotonic() - self._last_seen < self.alive_s

  def wait_for(self, t_eof: int, deadline_ns: int) -> tuple[np.ndarray | None, float]:
    """要 tEof 匹配的当帧 REPLY：已到且按时立即返回（零等待），否则等到 deadline。
    返回 (outputs[2066) f32, REPLY 往返 ms)；超时或迟到 (None, 0.)。结果一到就发，不等截止。
    """
    with self._cv:
      while True:
        r = self._last
        if r is not None and r.t_eof == t_eof:
          # 票面「按截止时刻择优」：迟到 REPLY（往返超预算）已在手也不用，落小模型兜底
          return (r.outputs, r.eof_to_reply_ms) if r.arrival_ns <= deadline_ns else (None, 0.)
        now_ns = nanos_since_boot()
        if now_ns >= deadline_ns:
          return None, 0.
        self._cv.wait((deadline_ns - now_ns) / 1e9)
