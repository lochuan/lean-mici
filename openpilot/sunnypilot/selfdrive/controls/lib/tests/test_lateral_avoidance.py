"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from opendbc.car.structs import car
from openpilot.cereal import log, messaging
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.modeld.constants import ModelConstants

from openpilot.sunnypilot.selfdrive.controls.lib.lateral_avoidance import (
  LateralAvoidancePlanner, fill_lateral_avoidance_msg,
  T_LOOKAHEAD, L_MIN, K_NUDGE_LIMIT, MIN_SPEED,
  RAMP_UP_TIME, ENTER_TIME,
)
from openpilot.sunnypilot.selfdrive.controls.lib.model_bias_source import BIAS_SIGN_FRAMES

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


def update(p, model_v2=None, points=None, v_ego=V_EGO, lat_active=True,
           left_blinker=False, right_blinker=False, left_bs=False, right_bs=False):
  p.update(model_v2 if model_v2 is not None else mock_model_v2(),
           points if points is not None else [], v_ego, lat_active,
           left_blinker, right_blinker, left_bs, right_bs)


def drive(p, seconds, **kwargs):
  for _ in range(int(seconds / DT_CTRL) + 1):
    update(p, **kwargs)


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


class TestLateralAvoidanceDisabledByDefault(OpenpilotTestCase):
  def test_feature_off_inert_with_real_params(self):
    p = LateralAvoidancePlanner()
    model_v2 = mock_model_v2()
    points = make_points(RIGHT_TARGET)
    for _ in range(500):  # ~5 s at 100 Hz
      p.update(model_v2, points, V_EGO, True, False, False, False, False)
    assert not p.active
    assert p.k_nudge == 0.0
    assert p.y_target == 0.0
