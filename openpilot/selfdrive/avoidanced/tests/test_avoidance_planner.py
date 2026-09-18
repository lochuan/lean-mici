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
  assert plan(targets=[_right_target()], max_offset=0.35, bsm_opposite=True) <= 0.12 + 1e-6
  assert plan(targets=[_right_target()], max_offset=0.35, bsm_opposite=False) <= 0.35 + 1e-6


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


class _Edge:
  def __init__(self, *ys, x=10.0):
    self.x = [x] * len(ys)
    self.y = list(ys)


def test_edge_clearance_min_over_lookahead():
  assert edge_clearance([_Edge(1.8, 1.6, 1.4)]) == pytest.approx(1.4)


def test_edge_clearance_side_filter():
  assert edge_clearance([_Edge(1.8, -0.4)], side=1) == pytest.approx(1.8)   # left only
  assert edge_clearance([_Edge(1.8, -0.4)], side=-1) == pytest.approx(0.4)  # right only


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
  # right target -> avoid left; a close LEFT edge (the avoidance side) blocks the plan
  _, valid = p.update(model_curvature=0.01, targets=[_right_target()], v_ego=20.0,
                      road_edges=[_Edge(0.5)], now=C.ENTER_HOLD_S + 0.01)
  assert not valid


def test_planner_clearance_is_direction_aware():
  # A close edge on the NON-avoidance side must not block the manoeuvre.
  p = _planner()
  _step(p, 0.0, [_right_target()])            # avoid left -> only left edges gate
  curv, valid = p.update(model_curvature=0.01, targets=[_right_target()], v_ego=20.0,
                         road_edges=[_Edge(-0.5)], now=C.ENTER_HOLD_S + 0.01)
  assert valid
  assert curv > 0.01

  # mirrored: left target -> avoid right -> only right edges gate; the bias now
  # pushes curvature below the model value (negative y_des).
  q = _planner()
  q.update(model_curvature=0.01, targets=[_left_target()], v_ego=20.0, road_edges=[_Edge(0.5)], now=0.0)
  curv, valid = q.update(model_curvature=0.01, targets=[_left_target()], v_ego=20.0,
                         road_edges=[_Edge(0.5)], now=C.ENTER_HOLD_S + 0.01)
  assert valid
  assert curv < 0.01


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


class _FakePubMaster:
  def __init__(self):
    self.sent = []

  def send(self, service, msg):
    self.sent.append((service, msg))


class _FakeParams:
  def __init__(self, enabled=True, max_offset=None):
    self._enabled = enabled
    self._max_offset = max_offset

  def get_bool(self, key, block=False):
    return self._enabled

  def get(self, key, block=False, return_default=False):
    return self._max_offset


class _FakeSubMaster:
  def __init__(self, model_v2, car_state, radar):
    self._data = {"modelV2": model_v2, "carState": car_state, "radarTracks": radar}
    self.valid = dict.fromkeys(self._data, True)

  def update(self, timeout=0):
    pass

  def __getitem__(self, service):
    return self._data[service]


class _NS:
  def __init__(self, **kwargs):
    self.__dict__.update(kwargs)


MODEL_CURVATURE = 0.012


def _make_daemon(enabled=True):
  from openpilot.selfdrive.avoidanced.avoidanced import AvoidanceDaemon
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[])
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_RadarPoint(8.0, -1.0)], errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = AvoidanceDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams(enabled=enabled))
  return daemon, pm


def test_daemon_sends_valid_flag_every_frame():
  daemon, pm = _make_daemon()
  daemon.update(0.0)                    # enter hysteresis not yet satisfied -> invalid
  daemon.update(C.ENTER_HOLD_S + 0.01)  # active -> valid

  assert len(pm.sent) == 4  # avoidanceDebug (2) + lateralManeuverPlan (2), one debug per frame
  service0, msg0 = pm.sent[1]
  service1, msg1 = pm.sent[3]
  assert service0 == service1 == "lateralManeuverPlan"
  assert msg0.valid is False
  assert msg0.lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)
  assert msg1.valid is True
  assert msg1.lateralManeuverPlan.desiredCurvature > MODEL_CURVATURE


def test_daemon_invalid_frame_carries_model_curvature():
  daemon, pm = _make_daemon(enabled=False)
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)

  assert len(pm.sent) == 4  # avoidanceDebug (2) + lateralManeuverPlan (2)
  for _, msg in pm.sent[1::2]:
    assert msg.valid is False
    assert msg.lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)
