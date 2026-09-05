"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from opendbc.car.structs import car

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.controls.lib.radar_source import (
  RadarSource, TRIGGER_MARGIN, MIN_TRACK_FRAMES, MAX_YREL_JUMP,
  SAME_DIRECTION_VREL_MIN, DREL_MIN, DREL_MAX,
)

D_LEFT = 1.8
D_RIGHT = 1.8


def make_points(specs):
  pts = []
  for tid, d, y, v in specs:
    pt = car.RadarData.RadarPoint()
    pt.trackId = tid
    pt.dRel = d
    pt.yRel = y
    pt.vRel = v
    pts.append(pt)
  return pts


def drive(src, specs, n, d_left=D_LEFT, d_right=D_RIGHT, enabled=True):
  for _ in range(n):
    src.update(make_points(specs), d_left, d_right, enabled)


class TestRadarSource(OpenpilotTestCase):
  def test_right_target_demands_left(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES)
    gap = 2.0 - D_RIGHT
    expected = (TRIGGER_MARGIN - gap) / TRIGGER_MARGIN
    assert abs(src.demand_left - expected) < 1e-6
    assert src.demand_right == 0.0

  def test_far_target_no_demand(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -3.5, 0.0)], MIN_TRACK_FRAMES)
    assert src.demand_left == 0.0

  def test_intrusion_full_demand(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -1.0, 0.0)], MIN_TRACK_FRAMES)
    assert src.demand_left == 1.0

  def test_left_target_demands_right(self):
    src = RadarSource()
    drive(src, [(1, 15.0, 2.0, 0.0)], MIN_TRACK_FRAMES)
    gap = 2.0 - D_LEFT
    expected = (TRIGGER_MARGIN - gap) / TRIGGER_MARGIN
    assert abs(src.demand_right - expected) < 1e-6
    assert src.demand_left == 0.0

  def test_track_continuity(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES - 1)
    assert src.demand_left == 0.0
    src.update(make_points([(1, 15.0, -2.0, 0.0)]), D_LEFT, D_RIGHT, True)
    assert src.demand_left > 0.0

  def test_oncoming_filtered(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -2.0, SAME_DIRECTION_VREL_MIN - 1.0)], MIN_TRACK_FRAMES)
    assert src.demand_left == 0.0

  def test_range_filtered(self):
    src = RadarSource()
    drive(src, [(1, DREL_MAX + 5.0, -2.0, 0.0), (2, DREL_MIN - 5.0, -2.0, 0.0)], MIN_TRACK_FRAMES)
    assert src.demand_left == 0.0

  def test_yrel_jump_resets(self):
    src = RadarSource()
    specs = [(1, 15.0, -2.0, 0.0)]
    drive(src, specs, MIN_TRACK_FRAMES)
    assert src.demand_left > 0.0
    src.update(make_points([(1, 15.0, -2.0 - MAX_YREL_JUMP * 2, 0.0)]), D_LEFT, D_RIGHT, True)
    assert src.demand_left == 0.0

  def test_stale_track_forgotten(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES)
    assert src.demand_left > 0.0
    drive(src, [], MIN_TRACK_FRAMES)
    drive(src, [(1, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES - 1)
    assert src.demand_left == 0.0

  def test_disabled(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES, enabled=False)
    assert src.demand_left == 0.0 and src.demand_right == 0.0

  def test_objects_filled(self):
    src = RadarSource()
    drive(src, [(7, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES)
    assert len(src.objects) == 1
    obj = src.objects[0]
    assert obj['source'] == 'radar' and obj['classId'] == -1
    assert obj['x'] == 15.0 and obj['y'] == -2.0 and obj['score'] == 1.0

  def test_reset(self):
    src = RadarSource()
    drive(src, [(1, 15.0, -2.0, 0.0)], MIN_TRACK_FRAMES)
    src.reset()
    assert src.demand_left == 0.0 and src.objects == []
