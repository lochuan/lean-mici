"""Tests for the 5Hz lateral avoidance planner (radar fusion + BSM gating)."""

from types import SimpleNamespace

import pytest

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.avoidance_planner import (AvoidancePlanner, Target, edge_clearance,
                                                              fuse_targets, plan)


class _RadarPoint:
  def __init__(self, dRel, yRel, vRel=0.0):
    self.dRel = dRel
    self.yRel = yRel
    self.vRel = vRel


def _right_target(dRel=5.0, w=C.VRU_WEIGHT, conf=1.0):
  # yRel=-1.8 must stay OUTSIDE the own-lane gate (|yRel| >= OWN_LANE_HALF_WIDTH
  # = 1.2, Task 6): a default inside the own lane gets gated out and silently
  # disables every test that relies on this helper driving a bias.
  return Target(side=-1, dRel=dRel, yRel=-1.8, w=w, conf=conf)


def _left_target(dRel=5.0, w=C.VRU_WEIGHT, conf=1.0):
  # Mirrored _right_target: same own-lane-gate constraint, see above.
  return Target(side=1, dRel=dRel, yRel=1.8, w=w, conf=conf)


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
  # vRel=-3.0: a moving point needs no vision confirmation (vRel=0.0 with the
  # default v_ego=0.0 is ground-static and now correctly requires one).
  # yRel=-1.8: outside the own-lane gate (Task 6) — |yRel|=1.0 is no longer a
  # target, so the fixture must sit in the adjacent-lane band to survive.
  targets = fuse_targets([_RadarPoint(10.0, -1.8, vRel=-3.0), _RadarPoint(5.0, 4.0), _RadarPoint(60.0, 0.5)])
  assert len(targets) == 1
  assert targets[0].dRel == 10.0
  assert targets[0].side == -1
  assert targets[0].w == C.VEHICLE_WEIGHT


def test_fuse_targets_uses_vru_weight_for_detections():
  targets = fuse_targets([], detections=[{"dRel": 8.0, "yRel": 1.2, "cls": "person", "conf": 0.9}])
  assert len(targets) == 1
  assert targets[0].w == C.VRU_WEIGHT
  assert targets[0].side == 1


# --- static radar needs vision confirmation (Task 5) --------------------------

def test_static_radar_point_needs_vision_confirmation():
  """护栏/桥墩的对地速度是 0 —— 但抛锚车也是。所以判据不是「动不动」,
  而是「有没有视觉确认」。"""
  guardrail = _RadarPoint(dRel=20.0, yRel=-2.0, vRel=-15.0)   # 对地速度 0
  targets = fuse_targets([guardrail], v_ego=15.0, confirmed_keys=())
  assert targets == []


def test_static_radar_point_is_kept_when_vision_confirms_it():
  stopped_car = _RadarPoint(dRel=20.0, yRel=-2.0, vRel=-15.0)
  targets = fuse_targets([stopped_car], v_ego=15.0, confirmed_keys=(id(stopped_car),))
  assert len(targets) == 1


def test_moving_radar_point_needs_no_confirmation():
  mover = _RadarPoint(dRel=20.0, yRel=-2.0, vRel=-3.0)        # 对地速度 12 m/s
  assert len(fuse_targets([mover], v_ego=15.0, confirmed_keys=())) == 1


def test_matched_radar_point_takes_the_vision_class_weight():
  """雷达看到的摩托车不该按 vehicle 算 —— 否则感知得越好权重越低。

  yRel=1.8(而非 1.0):Task 6 的本车道门要求 |yRel| >= OWN_LANE_HALF_WIDTH(1.2),
  本车道内的目标不再是避让目标 —— 夹具必须留在门外,否则 Task 6 会打破本测试。
  """
  moto = _RadarPoint(dRel=10.0, yRel=1.8, vRel=-1.0)
  targets = fuse_targets([moto], v_ego=15.0, confirmed_keys=(id(moto),),
                         vision_cls_by_key={id(moto): "motorcycle"})
  assert targets[0].w == C.VRU_WEIGHT


def test_missing_vrel_is_treated_as_static():
  """vRel 缺失时保守处理:按静止对待,需视觉确认。"""
  class NoVRel:
    dRel, yRel = 20.0, -2.0
  assert fuse_targets([NoVRel()], v_ego=15.0, confirmed_keys=()) == []


class _ReiteratedRadarPoints:
  """每次迭代都产出等价但全新的点对象 —— 模拟 pycapnp:每次访问 radar.points
  都构造新的 _DynamicStructReader 包装,id() 跨迭代必然不同。"""

  def __init__(self, **fields):
    self._fields = fields

  def __iter__(self):
    return iter([SimpleNamespace(**self._fields)])


def test_confirmation_and_class_weight_survive_radar_reiteration():
  """设备形态回归钉:radar.points 被迭代两次(associate 物化一次、fuse 再迭代
  一次)时,静止目标的视觉确认与类别权重必须仍然生效 —— 确认键跟 trackId 走,
  不跟 id() 走。把键逻辑退回 id()-only 会让本测试变红。"""
  points = _ReiteratedRadarPoints(dRel=10.0, yRel=1.8, vRel=-15.0, trackId=7)  # 对地速度 0
  first, second = list(points), list(points)
  assert id(first[0]) != id(second[0]) and first[0].dRel == second[0].dRel  # mimicry precondition
  targets = fuse_targets(points, v_ego=15.0, confirmed_keys={7},
                         vision_cls_by_key={7: "motorcycle"})
  assert len(targets) == 1              # 静止但有视觉确认 → 保留
  assert targets[0].w == C.VRU_WEIGHT   # 类别权重跟视觉走


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


# --- eagled process module ----------------------------------------------

def test_eagled_module_imports():
  from openpilot.selfdrive.eagled.eagled import EagleDaemon
  assert hasattr(EagleDaemon, "update")


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
    # Calibrated with zero rpy: identical to the pre-calibration mount constants
    # (C.CAMERA_PITCH / C.CAMERA_YAW are 0.0), so the vision path behaves exactly
    # as it did before live extrinsics were wired in.
    self._data = {"modelV2": model_v2, "carState": car_state, "radarTracks": radar,
                  "extrinsicsCalibration": _NS(calStatus="calibrated", rpyCalib=[0.0, 0.0, 0.0])}
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
  from openpilot.selfdrive.eagled.eagled import EagleDaemon
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  # yRel=-1.8: outside the own-lane gate (Task 6) — the target must survive the
  # gate for the planner to activate.
  radar = _NS(points=[_RadarPoint(8.0, -1.8)], errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams(enabled=enabled))
  return daemon, pm


def test_daemon_sends_valid_flag_every_frame():
  daemon, pm = _make_daemon()
  daemon.update(0.0)                    # enter hysteresis not yet satisfied -> invalid
  daemon.update(C.ENTER_HOLD_S + 0.01)  # active -> valid

  assert len(pm.sent) == 4  # eagleDebug (2) + lateralManeuverPlan (2), one debug per frame
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

  assert len(pm.sent) == 4  # eagleDebug (2) + lateralManeuverPlan (2)
  for _, msg in pm.sent[1::2]:
    assert msg.valid is False
    assert msg.lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)


# --- regression: BSM cap must not flip the avoidance side --------------------

def test_bsm_cap_does_not_reselect_target_on_the_other_side():
  """Target ranking must not depend on max_offset.

  Ranking by the capped magnitude let BSM's squeeze to MAX_OFFSET_BSM saturate
  several in-gate targets at the same value; the strict `>` tie-break then kept
  whichever came first and the chosen side could flip. update() derives the BSM
  gates from the uncapped direction, so a flip commanded a bias toward a side
  whose blind spot was never checked.
  """
  veh_right = Target(side=-1, dRel=10.0, yRel=-2.0, w=C.VEHICLE_WEIGHT, conf=1.0)
  vru_left = Target(side=1, dRel=20.0, yRel=2.0, w=C.VRU_WEIGHT, conf=1.0)
  targets = [veh_right, vru_left]  # fuse_targets emits radar points first

  # both saturate once capped, so only the ranking key keeps the side stable
  uncapped = plan(targets, max_offset=C.MAX_OFFSET_FREE)
  capped = plan(targets, max_offset=C.MAX_OFFSET_BSM)
  assert uncapped < 0.0, "vru_left has the highest desire -> avoid right"
  assert capped < 0.0, "capping must not move the bias to the other side"
  assert capped == pytest.approx(-C.MAX_OFFSET_BSM)


def test_planner_never_biases_into_an_occupied_blind_spot():
  veh_right = Target(side=-1, dRel=10.0, yRel=-2.0, w=C.VEHICLE_WEIGHT, conf=1.0)
  vru_left = Target(side=1, dRel=20.0, yRel=2.0, w=C.VRU_WEIGHT, conf=1.0)
  targets = [veh_right, vru_left]

  p = _planner()
  for i in range(20):
    _step(p, i * C.DT_5HZ, targets, v_ego=25.0, bsm_left=True)
  st = p.last_state
  # left blind spot is occupied -> the bias must not be positive (leftward)
  assert st["bias"] <= 0.0, f"biased {st['bias']:+.3f}m into an occupied left blind spot"
  # telemetry must agree with the action, otherwise shadow logs hide the fault
  assert st["direction"] < 0 and st["yDes"] <= 0.0


def test_reported_direction_matches_commanded_bias_sign():
  """last_state['direction'] feeds eagleDebug; it must match the bias."""
  for bsm in ({}, {"bsm_left": True}, {"bsm_right": True}):
    for targets in ([_right_target()], [_left_target()],
                    [Target(side=-1, dRel=10.0, yRel=-2.0, w=C.VEHICLE_WEIGHT, conf=1.0),
                     Target(side=1, dRel=20.0, yRel=2.0, w=C.VRU_WEIGHT, conf=1.0)]):
      p = _planner()
      for i in range(20):
        _step(p, i * C.DT_5HZ, targets, v_ego=25.0, **bsm)
      st = p.last_state
      if abs(st["yDes"]) > 1e-9:
        assert st["direction"] * st["yDes"] > 0.0, f"direction/yDes disagree for {bsm} {targets}"


def test_zero_max_offset_produces_no_manoeuvre():
  p = _planner()
  for i in range(20):
    curv, valid = _step(p, i * C.DT_5HZ, [_right_target()], max_offset=0.0)
  assert not valid
  assert curv == pytest.approx(0.01)


# --- own-lane gate + lane-change suppression (Task 6) -------------------------

def test_own_lane_target_is_not_a_target():
  """Y_GATE=2.5m 让 yRel~0 的目标也能触发, 而 _sign(0.0)=1 会让正前方目标
  固定往右让 0.35m —— 方向任意, 且 0.35m 绕不开本车道障碍。"""
  assert fuse_targets([_RadarPoint(dRel=20.0, yRel=0.0, vRel=-5.0)], v_ego=15.0) == []
  assert fuse_targets([_RadarPoint(dRel=20.0, yRel=1.0, vRel=-5.0)], v_ego=15.0) == []
  assert len(fuse_targets([_RadarPoint(dRel=20.0, yRel=1.5, vRel=-5.0)], v_ego=15.0)) == 1


def test_lane_change_suppresses_the_bias():
  p = _planner()
  _step(p, 0.0, [_right_target()], lane_change_active=True)
  curv, valid = _step(p, C.ENTER_HOLD_S + 0.01, [_right_target()], lane_change_active=True)
  assert not valid
  assert curv == pytest.approx(0.01)
