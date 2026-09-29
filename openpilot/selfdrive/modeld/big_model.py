# 大模型 outputs[0:2066) 解析 + 逐帧择优纯逻辑（04 号 C+D）。
# 择优口径（04 号票）：优先大模型、小模型兜底、运行时性能优先（结果一到就发不等截止、
# 不重试不补发）；无感切换 = 全头交叉淡入（SourceBlender）+ action 共用同一 smooth 平滑链。
# slices = MODEL_ABI §5 原始输出布局，裁掉 hidden_state/pad（状态在推理服务器，见 research 02）。
from collections import deque
from statistics import median

import numpy as np

from openpilot.selfdrive.modeld.parse_model_outputs import Parser

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
  assert raw.shape == (2066,), f"bad big outputs shape {raw.shape}"
  outs = {k: raw[v][np.newaxis] for k, v in BIG_OUTPUT_SLICES.items()}
  return Parser().parse_outputs(outs)


class LatencyEstimator:
  """L̂：最近约 100 帧 L_n 的滑动中位，限幅 [15, 35] ms，初值 22（ADR-0001）。

  L_n = modeld 收帧时刻 − timestamp_eof；每帧 L_n 由调用方进遥测。
  """
  def __init__(self, window: int = 100, lo: float = 15., hi: float = 35., init: float = 22.):
    self._s: deque[float] = deque(maxlen=window)
    self._lo, self._hi = lo, hi
    self.value = init

  def update(self, ln_ms: float) -> float:
    self._s.append(ln_ms)
    self.value = float(min(max(median(self._s), self._lo), self._hi))
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
