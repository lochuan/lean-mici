"""车道几何快照：modelV2 → 避让监测俯视图的车道数据源。

设计（2026-09-25 spec #1 / 票 #4，方案 A）：lanlinkd 自己订阅 modelV2，不动 capnp。
conflate socket 请求时取帧——/api/avoidance 约 2Hz，每请求最多解码一帧，
不跑后台解码循环。

坐标语义由 ``model_geometry`` 独占解释（spec #1）：本模块只做重采样与组装，
不做符号翻转/偏移换算。快照携带两套同网格几何：

- ``corrected``：``model_geometry(ctf=安装偏移)`` —— 车体系（x 前保险杠原点、
  y 左正），鸟瞰图默认层，与雷达目标/判定同原点。
- ``raw``：``model_geometry(ctf=0)`` —— 与换算前的展示行为逐点一致。

叠加视图两套曲线的错位 = 安装偏移本身，即精修仪器（自检：错位应等于
正在生效的 CAMERA_TO_FRONT）。
"""
from __future__ import annotations

import time

import numpy as np

from openpilot.cereal import messaging
from openpilot.common.model_geometry import geometry_to_vehicle_frame

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


def _line_entry(line, prob=None, std=None) -> dict | None:
  """车体系折线 → 快照项；线不可用返回 None。y 左正（model_geometry 出口）。"""
  if line is None:
    return None
  y = _resample(line.x, line.y)
  return {
    "y": y,
    "prob": float(prob) if prob is not None else None,
    "std": float(std) if std is not None else None,
  } if y is not None else None


def _geometry_set(model_v2, probs, stds, edge_stds, position, camera_to_front: float) -> dict:
  """一帧 modelV2 → 一套展示就绪几何（4 车道线 + 2 路沿 + 路径）。"""
  vgeo = geometry_to_vehicle_frame(model_v2, camera_to_front=camera_to_front)

  def _line_at(idx: int) -> dict | None:
    if probs is not None and len(probs) > idx and stds is not None and len(stds) > idx:
      prob, std = probs[idx], stds[idx]
    else:
      prob, std = None, None
    return _line_entry(vgeo.lane_lines[idx], prob=prob, std=std)

  def _edge_at(idx: int) -> dict | None:
    std = edge_stds[idx] if edge_stds is not None and len(edge_stds) > idx else None
    return _line_entry(vgeo.road_edges[idx], std=std)

  path = None
  if vgeo.path is not None:
    y = _resample(vgeo.path.x, vgeo.path.y)
    if y is not None:
      path = {
        "y": y,
        "std": _resample(vgeo.path.x, getattr(position, "yStd", None)),
      }

  return {
    "laneLines": [_line_at(i) for i in range(4)],
    "roadEdges": [_edge_at(i) for i in range(2)],
    "path": path,
  }


def lane_snapshot(model_v2, recv_mono: float, now_mono: float, camera_to_front: float) -> dict | None:
  """一帧 modelV2 → lanes dict；超龄返回 None。任何字段缺失降级为 None 项。

  ``camera_to_front`` 是注入的安装偏移值（精修结果的消费点，单一读点由调用方
  负责）。corrected/raw 两套几何同网格，逐线 quality 字段随各集携带。

  本车道边界的实/虚线裁决不用这里的 prob/std 重复阈值——直接消费
  eagleDebug 的 laneLeftValid/laneRightValid（eagled C7 门的结论）。
  prob 只用于外侧线的透明度显示。
  """
  if now_mono - recv_mono > LANE_MAX_AGE_S:
    return None
  probs = getattr(model_v2, "laneLineProbs", None)
  stds = getattr(model_v2, "laneLineStds", None)
  edge_stds = getattr(model_v2, "roadEdgeStds", None)
  position = getattr(model_v2, "position", None)

  return {
    "x": list(LANE_GRID_X),
    "corrected": _geometry_set(model_v2, probs, stds, edge_stds, position, camera_to_front),
    "raw": _geometry_set(model_v2, probs, stds, edge_stds, position, 0.0),
  }


class LaneCache:
  """请求时取帧的 modelV2 订阅（conflate，无后台循环）。

  只在 API handler 线程使用：SubMaster 惰性创建，snapshot() 每次调用
  先收一轮再判定新鲜度。超龄/无帧返回 None，调用方决定省略字段。
  ``camera_to_front`` 为安装偏移注入点（票 #6）：调用方每帧从读点取值注入。
  """

  def __init__(self, clock=time.monotonic):
    self._sm: messaging.SubMaster | None = None
    self._clock = clock

  def snapshot(self, camera_to_front: float) -> dict | None:
    if self._sm is None:
      try:
        self._sm = messaging.SubMaster(['modelV2'])
      except Exception:
        return None
    self._sm.update(0)
    return lane_snapshot(self._sm['modelV2'], self._sm.recv_time['modelV2'], self._clock(),
                         camera_to_front=camera_to_front)

  def stop(self) -> None:
    self._sm = None
