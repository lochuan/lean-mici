"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

VRU_CLASSES = (0, 1, 3)       # person, bicycle, motorcycle
TRUCK_CLASSES = (5, 7)        # bus, truck
CAR_CLASSES = (2,)            # car

CLASS_HALF_WIDTHS = {0: 0.3, 1: 0.5, 2: 0.95, 3: 0.5, 5: 1.3, 7: 1.3}
DEFAULT_HALF_WIDTH = 0.5

FUSE_GATE_X = 2.0             # m, radar association gate
FUSE_GATE_Y = 1.0
SAME_DIRECTION_VREL_MIN = -8.0
ONCOMING_VX_MARGIN = 5.0      # m/s, vx < -(v_ego + margin) -> oncoming
TRUCK_GAP_MAX = 1.5
TRUCK_DEMAND_MAX = 0.8
TRUCK_DEMAND_MIN = 0.5
TRUCK_DEMAND_SLOPE = 0.2
CAR_STRADDLE_GAP = 1.0


class VisionSource:
  def __init__(self):
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.objects: list[dict] = []
    self.matched_radar_track_ids: set[int] = set()

  def reset(self) -> None:
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.objects = []
    self.matched_radar_track_ids = set()

  def update(self, detections, radar_points, lane_lines, v_ego: float, vru_margin: float,
             vru_enabled: bool, truck_enabled: bool) -> None:
    self.reset()
    for det in detections:
      class_id = int(det.classId)
      is_vru = class_id in VRU_CLASSES
      if is_vru and not vru_enabled:
        continue
      if not is_vru and not truck_enabled:
        continue

      x, y, v_rel, matched_tid = float(det.x), float(det.y), None, -1
      best = None
      for pt in radar_points:
        if abs(pt.dRel - x) < FUSE_GATE_X and abs(pt.yRel - y) < FUSE_GATE_Y:
          d = (pt.dRel - x) ** 2 + (pt.yRel - y) ** 2
          if best is None or d < best[0]:
            best = (d, pt)
      if best is not None:
        x, y = float(best[1].dRel), float(best[1].yRel)
        v_rel = float(best[1].vRel)
        matched_tid = int(best[1].trackId)
        self.matched_radar_track_ids.add(matched_tid)

      half_width = CLASS_HALF_WIDTHS.get(class_id, DEFAULT_HALF_WIDTH)
      if y < 0.0:
        d_edge = -float(np.interp(x, lane_lines[2].x, lane_lines[2].y))
        center_gap = -y - d_edge
      else:
        d_edge = float(np.interp(x, lane_lines[1].x, lane_lines[1].y))
        center_gap = y - d_edge
      eff_gap = center_gap - half_width

      demand = 0.0
      if is_vru:
        if eff_gap < vru_margin:
          demand = float(np.clip((vru_margin - eff_gap) / vru_margin, 0.0, 1.0))
      elif class_id in TRUCK_CLASSES:
        same_direction = (v_rel is not None and v_rel > SAME_DIRECTION_VREL_MIN) or \
                         (v_rel is None and det.vx > -(v_ego + ONCOMING_VX_MARGIN))
        if same_direction and eff_gap < TRUCK_GAP_MAX:
          demand = float(np.clip(TRUCK_DEMAND_MAX - TRUCK_DEMAND_SLOPE * eff_gap,
                                 TRUCK_DEMAND_MIN, TRUCK_DEMAND_MAX))
      elif class_id in CAR_CLASSES:
        if eff_gap < CAR_STRADDLE_GAP:
          demand = float(np.clip((CAR_STRADDLE_GAP - eff_gap) / CAR_STRADDLE_GAP, 0.0, 1.0))

      if demand <= 0.0:
        continue
      if y < 0.0:
        self.demand_left = max(self.demand_left, demand)
      else:
        self.demand_right = max(self.demand_right, demand)
      self.objects.append({'source': 'vision', 'classId': class_id, 'x': x, 'y': y,
                           'demand': demand, 'score': float(det.score)})
