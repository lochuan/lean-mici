"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

SAME_DIRECTION_VREL_MIN = -8.0  # m/s, oncoming traffic has vRel ~= -2 * vEgo
DREL_MIN = -10.0                # m, slightly behind ego
DREL_MAX = 60.0                 # m
TRIGGER_MARGIN = 1.0            # m beyond our lane edge
MIN_TRACK_FRAMES = 3            # consecutive updates for a track to count
MAX_YREL_JUMP = 1.0             # m per update, ghost track protection


class RadarSource:
  def __init__(self):
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.objects: list[dict] = []
    self._track_frames: dict[int, int] = {}
    self._last_yrel: dict[int, float] = {}

  def reset(self) -> None:
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.objects = []
    self._track_frames.clear()
    self._last_yrel.clear()

  def update(self, radar_points, d_edge_left: float, d_edge_right: float, enabled: bool) -> None:
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.objects = []

    if not enabled:
      self._track_frames.clear()
      self._last_yrel.clear()
      return

    seen: set[int] = set()
    for pt in radar_points:
      tid = int(pt.trackId)
      seen.add(tid)
      d_rel = float(pt.dRel)
      y_rel = float(pt.yRel)
      v_rel = float(pt.vRel)

      if v_rel <= SAME_DIRECTION_VREL_MIN or not (DREL_MIN <= d_rel <= DREL_MAX):
        self._track_frames[tid] = 0
        continue

      last_y = self._last_yrel.get(tid)
      if last_y is not None and abs(y_rel - last_y) > MAX_YREL_JUMP:
        self._track_frames[tid] = 0
      self._last_yrel[tid] = y_rel

      self._track_frames[tid] = self._track_frames.get(tid, 0) + 1
      if self._track_frames[tid] < MIN_TRACK_FRAMES:
        continue

      gap = (y_rel - d_edge_left) if y_rel > 0 else (-y_rel - d_edge_right)
      intrusion = TRIGGER_MARGIN - gap
      if intrusion <= 0.0:
        continue
      demand = float(np.clip(intrusion / TRIGGER_MARGIN, 0.0, 1.0))

      if y_rel > 0:
        self.demand_right = max(self.demand_right, demand)
      else:
        self.demand_left = max(self.demand_left, demand)
      self.objects.append({'source': 'radar', 'classId': -1, 'x': d_rel, 'y': y_rel,
                           'demand': demand, 'score': 1.0})

    for tid in list(self._track_frames.keys()):
      if tid not in seen:
        del self._track_frames[tid]
    for tid in list(self._last_yrel.keys()):
      if tid not in seen:
        del self._last_yrel[tid]
