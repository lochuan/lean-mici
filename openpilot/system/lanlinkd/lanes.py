"""车道几何快照：modelV2 → 避让监测俯视图的车道数据源。

设计（2026-09-24 spec，方案 A）：lanlinkd 自己订阅 modelV2，不动 capnp。
conflate socket 请求时取帧——/api/avoidance 约 2Hz，每请求最多解码一帧，
不跑后台解码循环。

坐标约定（与 eagled gate_target 一致，视图与判定必须同坐标系）：
- modelV2 y 右正、x 以相机为原点；雷达 yRel 左正、x 以保险杠为原点。
- 这里只做 y 取负：``y_radar = -y_model``。x 直接当 dRel 用——eagled 的
  lane_geometry 插值就是这么用的（CAMERA_TO_FRONT 未换算是已记录的
  待修项，视图照它画，保持与判定一致）。
"""
from __future__ import annotations

import time

import numpy as np

from openpilot.cereal import messaging

# 俯视图显示范围：前方 0-60m。固定网格让前端不用处理不规则采样。
LANE_GRID_X = tuple(range(0, 65, 5))
LANE_MAX_AGE_S = 1.0     # modelV2 停更超过 1s 视为整体不可用
# laneLines 索引：0=远左外线 1=本道左边界 2=本道右边界 3=远右外线（openpilot 惯例）
_LIDX_OUTER_LEFT, _LIDX_LEFT, _LIDX_RIGHT, _LIDX_OUTER_RIGHT = 0, 1, 2, 3


def _resample(x, y) -> list[float] | None:
  """按共享网格插值；空线/缺数据返回 None（该线整体不可用）。"""
  if x is None or y is None or len(x) < 2 or len(y) < 2:
    return None
  return [float(v) for v in np.interp(list(LANE_GRID_X), np.asarray(x, dtype=float), np.asarray(y, dtype=float))]


def _line_entry(x, y, prob=None, std=None) -> dict:
  y = None if y is None else [-float(v) for v in y]   # 右正 -> 左正
  return {
    "y": _resample(x, y),
    "prob": float(prob) if prob is not None else None,
    "std": float(std) if std is not None else None,
  }


def lane_snapshot(model_v2, recv_mono: float, now_mono: float) -> dict | None:
  """一帧 modelV2 → lanes dict；超龄返回 None。任何字段缺失降级为 None 项。

  本车道边界的实/虚线裁决不用这里的 prob/std 重复阈值——直接消费
  eagleDebug 的 laneLeftValid/laneRightValid（eagled C7 门的结论）。
  prob 只用于外侧线的透明度显示。
  """
  if now_mono - recv_mono > LANE_MAX_AGE_S:
    return None
  lines = getattr(model_v2, "laneLines", None)
  probs = getattr(model_v2, "laneLineProbs", None)
  stds = getattr(model_v2, "laneLineStds", None)
  edges = getattr(model_v2, "roadEdges", None)
  edge_stds = getattr(model_v2, "roadEdgeStds", None)
  position = getattr(model_v2, "position", None)

  def _line_at(idx: int) -> dict | None:
    if lines is None or len(lines) <= idx:
      return None
    prob = float(probs[idx]) if probs is not None and len(probs) > idx else None
    std = float(stds[idx]) if stds is not None and len(stds) > idx else None
    entry = _line_entry(lines[idx].x, lines[idx].y, prob=prob, std=std)
    return entry if entry["y"] is not None else None

  def _edge_at(idx: int) -> dict | None:
    if edges is None or len(edges) <= idx:
      return None
    std = float(edge_stds[idx]) if edge_stds is not None and len(edge_stds) > idx else None
    entry = _line_entry(edges[idx].x, edges[idx].y, std=std)
    return entry if entry["y"] is not None else None

  path = None
  if position is not None:
    y = getattr(position, "y", None)
    y_radar = None if y is None else [-float(v) for v in y]   # 右正 -> 左正
    path = {
      "y": _resample(position.x, y_radar),
      "std": _resample(position.x, getattr(position, "yStd", None)),
    }
    if path["y"] is None:
      path = None

  return {
    "x": list(LANE_GRID_X),
    "laneLines": [_line_at(i) for i in range(4)],
    "roadEdges": [_edge_at(i) for i in range(2)],
    "path": path,
  }


class LaneCache:
  """请求时取帧的 modelV2 订阅（conflate，无后台循环）。

  只在 API handler 线程使用：SubMaster 惰性创建，snapshot() 每次调用
  先收一轮再判定新鲜度。超龄/无帧返回 None，调用方决定省略字段。
  """

  def __init__(self, clock=time.monotonic):
    self._sm: messaging.SubMaster | None = None
    self._clock = clock

  def snapshot(self) -> dict | None:
    if self._sm is None:
      try:
        self._sm = messaging.SubMaster(['modelV2'])
      except Exception:
        return None
    self._sm.update(0)
    return lane_snapshot(self._sm['modelV2'], self._sm.recv_time['modelV2'], self._clock())

  def stop(self) -> None:
    self._sm = None
