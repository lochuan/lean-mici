"""车道内避让(CONTEXT.md):本车道几何、侧向压力、车道内偏移与 controlsd 闭环修正。

只依赖 numpy 和 model_geometry,eagled(决策)与 controlsd(闭环 + 贴线钳制)共用,
两边对「本车道可信」「贴线上限」的理解只在这一处。坐标:车体系,x 前保险杠原点,y 左正。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

import numpy as np

from openpilot.common.constants import CV
from openpilot.common.model_geometry import geometry_to_vehicle_frame
from openpilot.selfdrive.eagled import constants as C


@dataclass(frozen=True)
class LaneGeometry:
  """Ego-lane boundaries + model path in the **vehicle frame** (y left-positive).

  ``left_valid``/``right_valid`` 是 C7 置信门（laneLineProbs/Stds）的逐边裁决。
  """
  left_valid: bool
  right_valid: bool
  left_x: tuple
  left_y: tuple
  right_x: tuple
  right_y: tuple
  path_x: tuple
  path_y: tuple
  path_std: tuple
  # 变道目标车道的外边界(laneLines[0]/[3]);置信不足时同宽推定,故只带 valid + 几何
  outer_left_valid: bool = False
  outer_left_x: tuple = ()
  outer_left_y: tuple = ()
  outer_right_valid: bool = False
  outer_right_x: tuple = ()
  outer_right_y: tuple = ()


def lane_geometry(model_v2, camera_to_front: float) -> LaneGeometry | None:
  """Extract ego-lane boundaries (laneLines[1]/[2]) + path with C7 quality flags.

  几何经 ``model_geometry`` 换算（解释权的唯一落点）。None = 模型几何整体不可用
  （字段缺失/列表为空）。对 duck-typed 测试桩安全：缺属性的假 modelV2 直接得 None。
  """
  probs = getattr(model_v2, "laneLineProbs", None)
  stds = getattr(model_v2, "laneLineStds", None)
  position = getattr(model_v2, "position", None)
  if probs is None or stds is None or position is None:
    return None
  if len(probs) <= C.LANE_IDX_RIGHT or len(stds) <= C.LANE_IDX_RIGHT:
    return None
  vgeo = geometry_to_vehicle_frame(model_v2, camera_to_front=camera_to_front)
  left, right = vgeo.lane_lines[C.LANE_IDX_LEFT], vgeo.lane_lines[C.LANE_IDX_RIGHT]
  if left is None or right is None:
    return None
  def confident(idx):
    return idx < min(len(probs), len(stds)) and float(probs[idx]) >= C.LANE_PROB_MIN and float(stds[idx]) <= C.LANE_STD_MAX

  left_valid, right_valid = confident(C.LANE_IDX_LEFT), confident(C.LANE_IDX_RIGHT)
  outer_left, outer_right = vgeo.lane_lines[C.LANE_IDX_OUTER_LEFT], vgeo.lane_lines[C.LANE_IDX_OUTER_RIGHT]
  path = vgeo.path
  path_x = path.x if path is not None else ()
  path_std = tuple(getattr(position, "yStd", ()) or ())
  return LaneGeometry(
    left_valid=left_valid, right_valid=right_valid,
    left_x=left.x, left_y=left.y,
    right_x=right.x, right_y=right.y,
    path_x=path_x, path_y=(path.y if path is not None else ()),
    path_std=path_std if len(path_std) == len(path_x) else (),
    outer_left_valid=outer_left is not None and confident(C.LANE_IDX_OUTER_LEFT),
    outer_left_x=outer_left.x if outer_left is not None else (),
    outer_left_y=outer_left.y if outer_left is not None else (),
    outer_right_valid=outer_right is not None and confident(C.LANE_IDX_OUTER_RIGHT),
    outer_right_x=outer_right.x if outer_right is not None else (),
    outer_right_y=outer_right.y if outer_right is not None else (),
  )


def lane_trusted(geo: LaneGeometry | None) -> bool:
  """本车道可用:左右两条车道线都过置信门。"""
  return geo is not None and geo.left_valid and geo.right_valid


def lane_offset_cap(geo: LaneGeometry) -> float:
  """贴线上限(m):本车道半宽 - 自车半宽 - 贴线余量,≤ 0 取 0。半宽取自车处到预瞄处全程最窄者。"""
  xs = np.linspace(0.0, C.L_LOOKAHEAD, 8)
  half = np.min((np.interp(xs, geo.left_x, geo.left_y) - np.interp(xs, geo.right_x, geo.right_y)) / 2.0)
  return max(0.0, float(half) - C.EGO_HALF_WIDTH - C.LANE_EDGE_MARGIN)


def lane_offset_correction(geo: LaneGeometry, desired_offset: float) -> float:
  """闭环曲率修正(1/m),叠加到当拍模型曲率。

  目标先按当拍车道线重算的贴线上限钳制;误差 = 钳制后目标 - 模型路径在预瞄处
  相对本车道中心的位置,换算 ``2·误差/预瞄²``。路径不可用 -> 0。
  """
  if len(geo.path_x) == 0:
    return 0.0
  cap = lane_offset_cap(geo)
  target = min(max(desired_offset, -cap), cap)
  look = C.L_LOOKAHEAD
  center = (np.interp(look, geo.left_x, geo.left_y) + np.interp(look, geo.right_x, geo.right_y)) / 2.0
  path_offset = np.interp(look, geo.path_x, geo.path_y) - center
  return float(2.0 * (target - path_offset) / look ** 2)


def approach_speed(v_rel: float | None, v_ego: float) -> float:
  """接近速度 = 自车速度 - 目标对地纵向速度(= -vRel)。vRel 未知(纯视觉)按假设速度保守推算。"""
  if v_rel is None:
    return max(v_ego - C.VISION_TARGET_SPEED, C.APPROACH_SPEED_MIN)
  return -v_rel


def target_pressure(dRel: float, yRel: float, cls, geo: LaneGeometry, approach: float) -> tuple[int, float, float]:
  """单个目标 -> (侧, 线距, 压力)。侧:+1 左邻 / -1 右邻 / 0 非邻道目标(中心在本车道内)。

  线距 = 目标靠近自车一侧的车身边缘到本车道对应车道线(目标 dRel 处插值)的横向距离,
  侵入为负。压力 ``clamp((触发线距 - 线距)/触发线距, 0, 1)``,触发线距按类别;
  dRel 不在 (0, 距离上限] 内、接近速度 ≤ 0、或到达时间 dRel/接近速度 超过时间窗口的目标压力为 0。
  """
  half = C.class_half_width(cls)
  left_line = float(np.interp(dRel, geo.left_x, geo.left_y))
  right_line = float(np.interp(dRel, geo.right_x, geo.right_y))
  if yRel > left_line:
    side, line_distance = 1, yRel - half - left_line
  elif yRel < right_line:
    side, line_distance = -1, right_line - (yRel + half)
  else:
    return 0, float("inf"), 0.0
  if not (0.0 < dRel <= C.TRIGGER_RANGE_MAX and approach > 0.0 and dRel / approach <= C.TIME_WINDOW_S):
    return side, line_distance, 0.0
  trigger = C.TRIGGER_LINE_DISTANCE_VRU if cls in C.VRU_CLASSES else C.TRIGGER_LINE_DISTANCE_VEHICLE
  return side, line_distance, min(max((trigger - line_distance) / trigger, 0.0), 1.0)


def _in_target_lane(d_rel: float, y_rel: float, geo: LaneGeometry, side: int) -> bool:
  """目标中心是否在 side(+1 左 / -1 右)的目标车道内:本车道该侧线与再外一条线之间。

  外侧线置信不足时按本车道同宽推定(多数路段外侧线置信偏低,这是默认回退)。
  """
  left = float(np.interp(d_rel, geo.left_x, geo.left_y))
  right = float(np.interp(d_rel, geo.right_x, geo.right_y))
  width = left - right
  if side > 0:
    outer = float(np.interp(d_rel, geo.outer_left_x, geo.outer_left_y)) if geo.outer_left_valid else left + width
    return left < y_rel <= outer
  outer = float(np.interp(d_rel, geo.outer_right_x, geo.outer_right_y)) if geo.outer_right_valid else right - width
  return outer <= y_rel < right


def _blocks_lane_change(t, v_ego: float) -> bool:
  """近区硬拦;速度未知不放宽;时间投影(目标 LEAD_TIME 秒后位置 ≤ 自车 EGO_TIME 秒后位置,carrotpilot 4s/3s)。"""
  if t.dRel <= C.LANE_CHANGE_NEAR_D or t.vRel is None:
    return True
  return t.dRel + (t.vRel + v_ego) * C.LANE_CHANGE_LEAD_TIME_S <= v_ego * C.LANE_CHANGE_EGO_TIME_S


def change_clear(targets: Iterable, geo: LaneGeometry | None, v_ego: float) -> tuple[str, str]:
  """变道清空(左, 右):"clear" / "blocked" / "unknown"(本车道线不可信)。

  只看经车道线确认位于目标车道内、dRel 在窗口内的目标;本车道的慢前车因此不拦往左超它。
  """
  if not lane_trusted(geo):
    return "unknown", "unknown"
  nearby = [t for t in targets if 0.0 < t.dRel <= C.SIDE_WINDOW_D]
  return tuple("blocked" if any(_in_target_lane(t.dRel, t.yRel, geo, side) and _blocks_lane_change(t, v_ego)
                                for t in nearby) else "clear" for side in (1, -1))


HOLD_MATCH_TOLERANCE = 3.0   # m, 可见目标 dRel 落在保持记录这一拍推算走过的区间(±此值)内,视为同一目标


@dataclass(frozen=True)
class _Hold:
  """一个邻道目标的并行保持记录:最后一次可见时的 dRel / 接近速度 / 压力,dRel 随推算收缩。"""
  side: int
  dRel: float
  approach: float
  pressure: float
  seen_t: float


@dataclass(frozen=True)
class OffsetDecision:
  valid: bool
  offset: float            # m, 车道内偏移(左正),已过速率限制
  cap: float               # m, 贴线上限
  pressure_left: float
  pressure_right: float
  reason: str              # 不生效原因;"" = 生效门全通
  holding: int = 0         # 正在并行保持(已离开视野、按推算保留压力)的目标数


class LaneOffsetPlanner:
  """压力 -> 车道内偏移。状态仅有速率限制用的当前偏移与上一拍时刻。"""

  def __init__(self) -> None:
    self._offset = 0.0
    self._last_t: float | None = None
    self._holds: list[_Hold] = []

  def update(self, targets: Iterable, geo: LaneGeometry | None, v_ego: float, now: float, *,
             enabled: bool, lat_active: bool, steering_pressed: bool, lane_change_active: bool) -> OffsetDecision:
    if not enabled:
      reason = "disabled"
    elif not lat_active:
      reason = "lat_inactive"
    elif steering_pressed:
      reason = "steering_pressed"
    elif lane_change_active:
      reason = "lane_change"
    elif not (C.V_EGO_MIN_KPH * CV.KPH_TO_MS <= v_ego <= C.V_EGO_MAX):
      reason = "speed"
    elif not lane_trusted(geo):
      reason = "lane_untrusted"
    else:
      reason = ""

    dt = C.DT_5HZ if self._last_t is None else min(max(now - self._last_t, 0.0), 1.0)
    self._last_t = now

    pressure = {1: 0.0, -1: 0.0}
    cap = 0.0
    holding = 0
    if reason:
      self._holds = []
    else:
      cap = lane_offset_cap(geo)
      seen = []   # 当前可见、在前方的邻道目标(含压力 0 的):用来认出哪些保持记录仍可见
      for t in targets:
        approach = approach_speed(t.vRel, v_ego)
        side, _line_distance, p = target_pressure(t.dRel, t.yRel, t.cls, geo, approach)
        if side:
          pressure[side] = max(pressure[side], p)
          if t.dRel > 0.0:
            seen.append(_Hold(side, t.dRel, approach, p, now))
      kept = self._advance_holds(seen, dt, now)
      holding = len(kept)
      for h in kept:
        pressure[h.side] = max(pressure[h.side], h.pressure)
      self._holds = kept + [h for h in seen if h.pressure > 0.0]
    target = cap * (pressure[-1] - pressure[1])

    step = C.OFFSET_RATE * dt
    self._offset += min(max(target - self._offset, -step), step)
    if not reason:
      self._offset = min(max(self._offset, -cap), cap)

    valid = not reason and (pressure[1] > 0.0 or pressure[-1] > 0.0 or abs(self._offset) > 1e-3)
    return OffsetDecision(valid, self._offset, cap, pressure[1], pressure[-1], reason, holding)

  def _advance_holds(self, seen: list[_Hold], dt: float, now: float) -> list[_Hold]:
    """推算上一拍的保持记录;仍可见的交还给 seen,已超过目标 + 车长余量或超时的丢弃,其余继续保持。"""
    kept = []
    for h in self._holds:
      lo, hi = h.dRel - max(h.approach, 0.0) * dt, h.dRel
      visible = any(s.side == h.side and lo - HOLD_MATCH_TOLERANCE <= s.dRel <= hi + HOLD_MATCH_TOLERANCE for s in seen)
      h = replace(h, dRel=lo)
      passed = h.dRel < -C.HOLD_PASS_MARGIN or now - h.seen_t > C.HOLD_MAX_S
      if not (visible or passed):
        kept.append(h)
    return kept
