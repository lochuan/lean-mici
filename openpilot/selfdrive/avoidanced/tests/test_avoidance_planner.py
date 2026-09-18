"""Tests for the 5Hz lateral avoidance planner (radar fusion + BSM gating)."""

import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.avoidance_planner import (AvoidancePlanner, Target, edge_clearance,
                                                              fuse_targets, plan)


class _RadarPoint:
  def __init__(self, dRel, yRel, vRel=0.0):
    self.dRel = dRel
    self.yRel = yRel
    self.vRel = vRel


def _right_target(dRel=5.0, w=C.VRU_WEIGHT, conf=1.0):
  return Target(side=-1, dRel=dRel, yRel=-1.0, w=w, conf=conf)


def _left_target(dRel=5.0, w=C.VRU_WEIGHT, conf=1.0):
  return Target(side=1, dRel=dRel, yRel=1.0, w=w, conf=conf)


# --- brief Step 1 (verbatim) -------------------------------------------------

def test_bsm_gates_offset():
  assert plan(max_offset=0.35, bsm_opposite=True) <= 0.12 + 1e-6
  assert plan(max_offset=0.35, bsm_opposite=False) <= 0.35 + 1e-6


def test_no_target_no_bias():
  assert plan(targets=[]) == 0.0


# --- BSM gating --------------------------------------------------------------

def test_bsm_gate_is_not_vacuous():
  # Without the gate a close target saturates max_offset; the gate must cap it.
  assert plan(targets=[_right_target()], max_offset=0.35, bsm_opposite=False) == pytest.approx(0.35)
  assert plan(targets=[_right_target()], max_offset=0.35, bsm_opposite=True) == pytest.approx(C.MAX_OFFSET_BSM)


def test_bsm_same_side_forbids_bias():
  assert plan(targets=[_right_target()], bsm_same=True) == 0.0


def test_bsm_both_sides_zero():
  # Process maps "both BSM active" to bsm_same on whichever side it would avoid.
  assert plan(targets=[_right_target()], bsm_same=True, bsm_opposite=True) == 0.0


# --- sign / magnitude --------------------------------------------------------

def test_avoid_direction_is_away_from_target():
  assert plan(targets=[_right_target()]) > 0.0
  assert plan(targets=[_left_target()]) < 0.0


def test_offset_capped_by_max_offset():
  assert abs(plan(targets=[_right_target()], max_offset=0.2)) <= 0.2 + 1e-6


def test_proximity_ramp_prefers_near_target():
  near = abs(plan(targets=[_right_target(dRel=5.0)]))
  far = abs(plan(targets=[_right_target(dRel=45.0)]))
  assert far < near


def test_lateral_gate_drops_out_of_lane_target():
  assert plan(targets=[Target(side=1, dRel=5.0, yRel=C.Y_GATE + 0.5, w=C.VRU_WEIGHT, conf=1.0)]) == 0.0


def test_vru_weight_exceeds_vehicle_weight():
  vru = abs(plan(targets=[_right_target(w=C.VRU_WEIGHT)]))
  vehicle = abs(plan(targets=[_right_target(w=C.VEHICLE_WEIGHT)]))
  assert vru >= vehicle


# --- fusion ------------------------------------------------------------------

def test_fuse_targets_from_radar_points():
  targets = fuse_targets([_RadarPoint(10.0, -1.0), _RadarPoint(5.0, 4.0), _RadarPoint(60.0, 0.5)])
  assert len(targets) == 1
  assert targets[0].dRel == 10.0
  assert targets[0].side == -1
  assert targets[0].w == C.VEHICLE_WEIGHT


def test_fuse_targets_uses_vru_weight_for_detections():
  targets = fuse_targets([], detections=[{"dRel": 8.0, "yRel": 1.2, "cls": "person", "conf": 0.9}])
  assert len(targets) == 1
  assert targets[0].w == C.VRU_WEIGHT
  assert targets[0].side == 1


def test_edge_clearance_min_over_lookahead():
  class _Edge:
    x = [0.0, 10.0, 30.0]
    y = [1.8, 1.6, 1.4]

  assert edge_clearance([_Edge()]) == pytest.approx(1.4)


# --- AvoidancePlanner gating / temporal behaviour ----------------------------

def _planner(**kwargs):
  p = AvoidancePlanner()
  return p


def _step(p, now, targets, **kwargs):
  kwargs.setdefault("v_ego", 20.0)
  return p.update(model_curvature=0.01, targets=targets, now=now, **kwargs)


def test_planner_enter_hysteresis():
  p = _planner()
  _, valid0 = _step(p, 0.0, [_right_target()])
  _, valid1 = _step(p, C.ENTER_HOLD_S - 0.01, [_right_target()])
  _, valid2 = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()])
  assert not valid0 and not valid1
  assert valid2


def test_planner_exit_hysteresis():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  _, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()])
  assert valid
  _, still = _step(p, C.ENTER_HOLD_S + 0.01 + C.EXIT_HOLD_S - 0.01, [])
  _, gone = _step(p, C.ENTER_HOLD_S + 0.01 + C.EXIT_HOLD_S + 0.01, [])
  assert still
  assert not gone


def test_planner_low_speed_invalid():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  _, valid = p.update(model_curvature=0.01, targets=[_right_target()], v_ego=C.V_EGO_MIN - 1.0,
                      now=C.ENTER_HOLD_S + 0.01)
  assert not valid


def test_planner_high_speed_invalid():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  _, valid = p.update(model_curvature=0.01, targets=[_right_target()], v_ego=C.V_EGO_MAX + 1.0,
                      now=C.ENTER_HOLD_S + 0.01)
  assert not valid


def test_planner_edge_clearance_gate():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  _, valid = p.update(model_curvature=0.01, targets=[_right_target()], v_ego=20.0,
                      edge_clearance=C.EDGE_CLEAR_MIN - 0.1, now=C.ENTER_HOLD_S + 0.01)
  assert not valid


def test_planner_takeover_invalid():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  _, valid = p.update(model_curvature=0.01, targets=[_right_target()], v_ego=20.0,
                      steering_pressed=True, now=C.ENTER_HOLD_S + 0.01)
  assert not valid


def test_planner_bias_is_lowpassed_and_added_to_model():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  curv, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()])
  assert valid
  raw_bias = 2.0 * C.MAX_OFFSET_FREE / C.L_LOOKAHEAD ** 2
  assert curv > 0.01
  assert curv < 0.01 + raw_bias


def test_planner_invalid_falls_back_to_model():
  p = _planner()
  _step(p, 0.0, [_right_target()])
  curv, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()], v_ego=C.V_EGO_MIN - 1.0)
  assert not valid
  assert curv == pytest.approx(0.01)


def test_planner_bsm_same_side_forbids_bias():
  # target on the right -> avoid left; a left BSM sits on the avoidance side.
  p = _planner()
  _step(p, 0.0, [_right_target()], bsm_left=True)
  curv, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()], bsm_left=True)
  assert valid
  assert curv == pytest.approx(0.01)


def test_planner_bsm_opposite_side_caps_bias():
  # a right BSM is opposite the left avoidance -> bias capped at the BSM档.
  p = _planner()
  _step(p, 0.0, [_right_target()], bsm_right=True)
  curv, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()], bsm_right=True)
  assert valid
  raw_bias = 2.0 * C.MAX_OFFSET_BSM / C.L_LOOKAHEAD ** 2
  assert 0.01 < curv < 0.01 + raw_bias


def test_planner_both_bsm_sides_zero():
  p = _planner()
  _step(p, 0.0, [_right_target()], bsm_left=True, bsm_right=True)
  curv, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()], bsm_left=True, bsm_right=True)
  assert valid
  assert curv == pytest.approx(0.01)


# --- avoidanced process module ----------------------------------------------

def test_avoidanced_module_imports():
  from openpilot.selfdrive.avoidanced.avoidanced import AvoidanceDaemon
  assert hasattr(AvoidanceDaemon, "update")
