"""雷达 <-> 视觉关联,按方位角匹配并双向互斥。

为什么是方位角而不是笛卡尔距离:单目相机测得准的是方位角,测不准的是距离。
地平面投影的 dRel 在 40m 处对 0.5deg pitch 误差就放大到 41%,而旧实现的关联门
是 dx<=2m —— 建在最不可靠的轴上,远处几乎永不匹配,P0 的「关联率>0.80」因此
物理上不可达。

匹配上的目标:距离取雷达(可靠)、类别取视觉。这修掉一个反向激励 —— 旧实现
让匹配上的雷达点一律按 VEHICLE_WEIGHT 算,于是一辆被雷达看到的摩托车权重
(0.6)反而低于雷达漏检的摩托车(1.0)。
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from openpilot.selfdrive.avoidanced.constants import ASSOC_MAX_DBEARING, ASSOC_MAX_DY_M
from openpilot.selfdrive.avoidanced.ranging import range_from_box_height


def _radar_bearing(point) -> float:
  d, y = (float(point["dRel"]), float(point["yRel"])) if isinstance(point, dict) \
    else (float(point.dRel), float(point.yRel))
  return math.atan2(-y, d)          # yRel 左正, bearing 右正


def _radar_range(point) -> float:
  return float(point["dRel"]) if isinstance(point, dict) else float(point.dRel)


def _vision_bearing(obj) -> float:
  """视觉侧方位角:投影 dict 自带 ``bearing``;对象侧(shadow 的 VisionObject)
  从它自己的车体坐标推出 —— bearing 向图像右为正而 y 向左为正,故取 ``atan2(-y, x)``。"""
  if isinstance(obj, dict):
    return float(obj["bearing"])
  return math.atan2(-float(obj.y), float(obj.x))


def _vision_gate_range(obj, fy: float) -> float | None:
  """二级横向门的视觉侧距离;``None`` 表示跳过该门。

  优先框高测距(cls + boxHeightPx 都在时);否则对象若暴露 ``x``(它的车体
  距离)就用它;都没有则不做二级校验。
  """
  if isinstance(obj, dict):
    cls, h_px = obj.get("cls"), obj.get("boxHeightPx")
    if cls is not None and h_px is not None:
      return range_from_box_height(float(h_px), fy, cls)
    return None
  d = None
  cls, h_px = getattr(obj, "cls", None), getattr(obj, "boxHeightPx", None)
  if cls is not None and h_px is not None:
    d = range_from_box_height(float(h_px), fy, cls)
  if d is None:
    x = getattr(obj, "x", None)
    d = float(x) if x is not None else None
  return d


def _vision_cls(obj):
  return obj.get("cls") if isinstance(obj, dict) else getattr(obj, "cls", None)


def nearest_pairs_by_bearing(radar_points: Iterable, vision_objects: Iterable, fy: float,
                             max_dbearing: float, max_dy: float) -> tuple[list[tuple], set[int]]:
  """按 |Δbearing| 升序贪心匹配, 雷达点与视觉目标各自只能配一次。

  返回 ``(pairs, matched_vision_idx)``, ``pairs`` 每项是
  ``(radar, vision, dbearing, vision_cls)``。
  """
  radars = list(radar_points)
  vision = list(vision_objects)

  cands: list[tuple[float, int, int]] = []
  for ri, radar in enumerate(radars):
    rb, rd = _radar_bearing(radar), _radar_range(radar)
    for vi, obj in enumerate(vision):
      db = abs(rb - _vision_bearing(obj))
      if db > max_dbearing:
        continue
      # 二级校验: 框高测距与雷达距离的横向差不得离谱
      vd = _vision_gate_range(obj, fy)
      if vd is not None and abs(vd - rd) * math.cos(rb) > max_dy:
        continue
      cands.append((db, ri, vi))

  cands.sort()
  used_r: set[int] = set()
  used_v: set[int] = set()
  pairs: list[tuple] = []
  for db, ri, vi in cands:
    if ri in used_r or vi in used_v:
      continue
    used_r.add(ri)
    used_v.add(vi)
    pairs.append((radars[ri], vision[vi], db, _vision_cls(vision[vi])))
  return pairs, used_v


def associate(radar_points: Iterable, detections: Iterable, fy: float,
              max_dbearing: float = ASSOC_MAX_DBEARING,
              max_dy: float = ASSOC_MAX_DY_M) -> tuple[int, list[dict], list[tuple]]:
  """``(n_associated, fused, pairs)``。

  ``fused`` 只含**未匹配**的视觉目标 —— 雷达漏检的那些, 典型是 VRU。它们的距离
  来自框高测距(不依赖 pitch), 横向由方位角推出。无法定距的直接丢弃, 因为一个
  没有距离的目标进不了 planner 的 gate。
  """
  detections = list(detections)
  matches, matched = nearest_pairs_by_bearing(radar_points, detections, fy, max_dbearing, max_dy)

  fused: list[dict] = []
  for idx, det in enumerate(detections):
    if idx in matched:
      continue
    d = range_from_box_height(float(det.get("boxHeightPx", 0.0)), fy, det.get("cls"))
    if d is None or d <= 0.0:
      continue
    out = dict(det)
    out["dRel"] = d
    out["yRel"] = -d * math.tan(float(det["bearing"]))
    out["dRelSource"] = "boxheight"
    fused.append(out)

  pairs = [(radar, obj, pid, cls)
           for pid, (radar, obj, _db, cls) in enumerate(matches, start=1)]
  return len(matches), fused, pairs
