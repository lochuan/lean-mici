"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.modeld.constants import ModelConstants

from openpilot.sunnypilot.selfdrive.controls.lib.lateral_avoidance import (
  LateralAvoidancePlanner, fill_lateral_avoidance_msg,
  T_LOOKAHEAD, L_MIN, K_NUDGE_LIMIT, MIN_SPEED,
  RAMP_UP_TIME, RAMP_DOWN_TIME, ENTER_TIME, EXIT_TIME,
)
from openpilot.sunnypilot.selfdrive.controls.lib.model_bias_source import BIAS_SIGN_FRAMES
from openpilot.sunnypilot.selfdrive.controls.lib.vision_source import VisionSource

X_IDXS = ModelConstants.X_IDXS

V_EGO = 25.0
STEADY = ENTER_TIME + RAMP_UP_TIME + 0.5  # s, enough to fully engage


def planner(mocker):
  mocker.patch("openpilot.sunnypilot.selfdrive.controls.lib.lateral_avoidance.Params")
  p = LateralAvoidancePlanner()
  p.params.get_bool.side_effect = lambda key, block=False: True
  p.params.get.side_effect = lambda key, return_default=False: {
    "LateralAvoidanceMaxOffset": "0.40",
    "LateralAvoidanceLineMargin": "0.30",
    "LateralAvoidanceVRUMargin": "1.00",
  }.get(key, "0.0")
  p.enabled = True
  return p


def mock_model_v2(path_y=0.0, ll_left=1.8, ll_right=-1.8, probs=(0.9, 0.9, 0.9, 0.9),
                  curv=0.0, lane_change_state=0):
  m = log.ModelDataV2.new_message()
  m.action.desiredCurvature = curv
  m.meta.laneChangeState = lane_change_state
  m.position.x = list(X_IDXS)
  m.position.y = [path_y * min(1.0, x / 30.0) if x > 0 else 0.0 for x in X_IDXS]
  lines = m.init('laneLines', 4)
  for i, y_val in enumerate([-5.0, ll_left, ll_right, 5.0]):
    lines[i].x = list(X_IDXS)
    lines[i].y = [y_val] * len(X_IDXS)
  m.laneLineProbs = list(probs)
  return m


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


RIGHT_TARGET = [(1, 15.0, -2.0, 0.0)]  # gap = 0.2 -> demand 0.8


def update(p, model_v2=None, points=None, dets=None, v_ego=V_EGO, lat_active=True,
           left_blinker=False, right_blinker=False, left_bs=False, right_bs=False):
  p.update(model_v2 if model_v2 is not None else mock_model_v2(),
           points if points is not None else [],
           dets if dets is not None else [],
           v_ego, lat_active, left_blinker, right_blinker, left_bs, right_bs)


def drive(p, seconds, **kwargs):
  for _ in range(int(seconds / DT_CTRL) + 1):
    update(p, **kwargs)


class Det:
  def __init__(self, class_id, score, x, y, vx=0.0):
    self.classId = class_id
    self.score = score
    self.x = x
    self.y = y
    self.vx = vx


class TestLateralAvoidancePlanner(OpenpilotTestCase):
  def test_disabled_by_param(self, mocker):
    p = planner(mocker)
    p.params.get_bool.side_effect = lambda key, block=False: key != "LateralAvoidanceEnabled"
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert p.k_nudge == 0.0 and not p.active

  def test_disengaged_resets(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert p.active
    drive(p, 0.1, points=make_points(RIGHT_TARGET), lat_active=False)
    assert p.k_nudge == 0.0 and not p.active and p._envelope == 0.0

  def test_radar_avoidance_moves_left(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert p.active
    assert p.y_target > 0.0   # move left away from right-side target
    assert p.k_nudge > 0.0
    L = max(V_EGO * T_LOOKAHEAD, L_MIN)
    assert abs(p.k_nudge - 2.0 * p.y_target / (L * L)) < 1e-9  # y_ego = 0 here

  def test_offset_clamped_to_lane(self, mocker):
    p = planner(mocker)
    # left line at 1.5 m -> max_left = 1.5 - 1.0 - 0.3 = 0.2 < requested 0.32
    drive(p, STEADY, model_v2=mock_model_v2(ll_left=1.5, ll_right=-1.9), points=make_points(RIGHT_TARGET))
    assert p.active
    assert abs(p.y_target - 0.2) < 1e-6

  def test_both_sides_cancel(self, mocker):
    p = planner(mocker)
    both = RIGHT_TARGET + [(2, 15.0, 2.0, 0.0)]
    drive(p, STEADY, points=make_points(both))
    assert abs(p.y_target) < 1e-6

  def test_blinker_disables(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert p.active
    update(p, points=make_points(RIGHT_TARGET), left_blinker=True)
    assert p.k_nudge == 0.0

  def test_lane_change_state_disables(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    update(p, model_v2=mock_model_v2(lane_change_state=1), points=make_points(RIGHT_TARGET))
    assert p.k_nudge == 0.0

  def test_bsm_suppression(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert p.y_target > 0.0
    drive(p, 0.1, points=make_points(RIGHT_TARGET), left_bs=True)
    assert p.y_target == 0.0

  def test_bsm_away_side_not_suppressed(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert p.y_target > 0.0
    drive(p, 0.1, points=make_points(RIGHT_TARGET), right_bs=True)
    assert p.y_target > 0.0  # moving left, away from a right-side target, is not blocked by right BSM

  def test_lane_width_sanity(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, model_v2=mock_model_v2(ll_left=0.6, ll_right=-0.6), points=make_points(RIGHT_TARGET))
    assert p.k_nudge == 0.0 and not p.active

  def test_low_prob_disables(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, model_v2=mock_model_v2(probs=(0.9, 0.3, 0.9, 0.9)), points=make_points(RIGHT_TARGET))
    assert p.k_nudge == 0.0

  def test_low_speed_disables(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET), v_ego=MIN_SPEED - 1.0)
    assert p.k_nudge == 0.0

  def test_model_bias_integrated(self, mocker):
    p = planner(mocker)
    drive(p, BIAS_SIGN_FRAMES / 20.0 + STEADY, model_v2=mock_model_v2(path_y=0.4))
    assert p.active
    assert abs(p.y_target - 0.12) < 0.01  # 0.4 * 0.3 (interp at x_la=37.5 gives ~0.116)
    assert p.k_nudge > 0.0

  def test_k_nudge_limited(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET), v_ego=MIN_SPEED + 1.0)
    assert abs(p.k_nudge) <= K_NUDGE_LIMIT

  def test_radar_param_off(self, mocker):
    p = planner(mocker)
    p.params.get_bool.side_effect = lambda key, block=False: key != "LateralAvoidanceTruckEnabled"
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    assert not p.active and p.k_nudge == 0.0

  def test_fill_message(self, mocker):
    p = planner(mocker)
    drive(p, STEADY, points=make_points(RIGHT_TARGET))
    msg = messaging.new_message('lateralAvoidanceSP')
    fill_lateral_avoidance_msg(msg.lateralAvoidanceSP, p)
    la = msg.lateralAvoidanceSP
    assert la.active
    assert abs(la.yTarget - p.y_target) < 1e-6
    assert la.demandLeft > 0.0 and la.demandRight == 0.0
    assert len(la.objects) == 1
    assert la.objects[0].source == 1  # radar
    assert la.objects[0].classId == -1
    assert abs(la.objects[0].y - (-2.0)) < 1e-6

  def test_vision_drives_avoidance(self, mocker):
    p = planner(mocker)
    dets = [Det(1, 0.8, 15.0, -2.5)]
    drive(p, STEADY, points=[], dets=dets)
    assert p.active
    assert p.y_target > 0.0   # move left away from right-side VRU
    assert len(p.objects) == 1 and p.objects[0]['source'] == 'vision'

  def test_vision_matches_dedupe_radar(self, mocker):
    p = planner(mocker)
    pts = make_points([(42, 15.0, -2.3, 0.0)])
    dets = [Det(2, 0.8, 15.0, -2.5)]
    drive(p, STEADY, points=pts, dets=dets)
    assert len(p.objects) == 1  # radar track matched by vision -> only vision object remains

  def test_stale_vision_detections_release_demand(self, mocker):
    # controlsd passes [] when vruDetectionsSP goes stale (daemon dead); demand must decay back
    p = planner(mocker)
    dets = [Det(7, 0.9, 15.0, -3.7)]  # right-side truck
    drive(p, STEADY, points=[], dets=dets)
    assert p.y_target > 0.0
    drive(p, EXIT_TIME + RAMP_DOWN_TIME + 0.5, points=[], dets=[])
    assert p.demand_left == 0.0 and p.demand_right == 0.0
    assert p.objects == [] and p.y_target == 0.0 and not p.active


class TestLateralAvoidanceDisabledByDefault(OpenpilotTestCase):
  def test_feature_off_inert_with_real_params(self):
    p = LateralAvoidancePlanner()
    model_v2 = mock_model_v2()
    points = make_points(RIGHT_TARGET)
    for _ in range(500):  # ~5 s at 100 Hz
      p.update(model_v2, points, [], V_EGO, True, False, False, False, False)
    assert not p.active
    assert p.k_nudge == 0.0
    assert p.y_target == 0.0


class TestVisionSource(OpenpilotTestCase):
  def make_lines(self, left=1.8, right=-1.8):
    m = mock_model_v2(ll_left=left, ll_right=right)
    return m.laneLines

  def test_vru_right_side_demands_left(self):
    src = VisionSource()
    src.update([Det(1, 0.8, 15.0, -2.5)], [], self.make_lines(), 25.0, 1.0, True, True)
    # center_gap = 2.5 - 1.8 = 0.7; eff = 0.7 - 0.5 = 0.2; demand = (1.0-0.2)/1.0 = 0.8
    np.testing.assert_allclose(src.demand_left, 0.8, atol=1e-6)
    assert src.demand_right == 0.0
    assert len(src.objects) == 1 and src.objects[0]['source'] == 'vision'

  def test_vru_far_no_demand(self):
    src = VisionSource()
    src.update([Det(1, 0.8, 15.0, -3.5)], [], self.make_lines(), 25.0, 1.0, True, True)
    assert src.demand_left == 0.0 and src.objects == []

  def test_vru_left_side_demands_right(self):
    src = VisionSource()
    src.update([Det(1, 0.8, 15.0, 2.5)], [], self.make_lines(), 25.0, 1.0, True, True)
    np.testing.assert_allclose(src.demand_right, 0.8, atol=1e-6)

  def test_truck_unfused_uses_vx(self):
    src = VisionSource()
    # parallel truck: center 3.7, half 1.3 -> eff gap = 3.7-1.8-1.3 = 0.6; demand = 0.8-0.2*0.6
    src.update([Det(7, 0.9, 5.0, -3.7, vx=0.0)], [], self.make_lines(), 25.0, 1.0, True, True)
    np.testing.assert_allclose(src.demand_left, 0.8 - 0.2 * 0.6, atol=1e-6)

  def test_truck_oncoming_excluded(self):
    src = VisionSource()
    src.update([Det(7, 0.9, 5.0, -3.7, vx=-20.0)], [], self.make_lines(), 5.0, 1.0, True, True)
    assert src.demand_left == 0.0  # vx < -(v_ego + 5) = -10

  def test_truck_fused_with_radar(self):
    src = VisionSource()
    pts = make_points([(42, 5.0, -3.7, -1.0)])
    src.update([Det(7, 0.9, 4.5, -3.3, vx=0.0)], pts, self.make_lines(), 25.0, 1.0, True, True)
    obj = src.objects[0]
    np.testing.assert_allclose([obj['x'], obj['y']], [5.0, -3.7], atol=1e-6)  # radar position wins
    assert src.matched_radar_track_ids == {42}
    np.testing.assert_allclose(src.demand_left, 0.8 - 0.2 * 0.6, atol=1e-6)

  def test_car_straddling(self):
    src = VisionSource()
    # center 2.4, half 0.95 -> eff = 2.4-1.8-0.95 = -0.35 -> demand 1.0
    src.update([Det(2, 0.8, 10.0, -2.4)], [], self.make_lines(), 25.0, 1.0, True, True)
    np.testing.assert_allclose(src.demand_left, 1.0, atol=1e-6)

  def test_vru_disabled(self):
    src = VisionSource()
    src.update([Det(1, 0.8, 15.0, -2.5)], [], self.make_lines(), 25.0, 1.0, False, True)
    assert src.demand_left == 0.0

  def test_lane_line_interpolated(self):
    src = VisionSource()
    lines = self.make_lines()
    # mutate right line to slope: -1.8 @ x=0 -> -2.8 @ x=100
    lines[2].y = [-1.8 - x / 100.0 for x in lines[2].x]
    src.update([Det(1, 0.8, 50.0, -3.0)], [], lines, 25.0, 1.0, True, True)
    # right edge at x=50: -2.3; center_gap = 3.0-2.3 = 0.7; eff = 0.2 -> demand 0.8
    np.testing.assert_allclose(src.demand_left, 0.8, atol=1e-6)
