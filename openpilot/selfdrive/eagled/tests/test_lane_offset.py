"""切口 ①:EagleDaemon.update——邻道目标 → 侧向压力 → 车道内偏移(CONTEXT.md)。

断言只看发布出去的消息字段(lateralManeuverPlan / eagleDebug / eagleState)。
车体系 y 左正:左邻目标 yRel > 0。默认 3.5m 车道(半宽 1.75)、自车半宽 0.9、
贴线余量 0.15 → 贴线上限 0.70m。无类别雷达点按机动车处理(触发线距 0.5m、半宽 0.5m)。
压力例:右侧点 yRel = -(1.75 + 0.5 + 线距)。
"""
import pytest

from openpilot.selfdrive.eagled.eagled import EagleDaemon
from openpilot.selfdrive.eagled.lane_offset import approach_speed
from openpilot.selfdrive.eagled.tests.test_daemon_fusion import ROI, _box_at, _FakeCamera, _FakeDetector, _FakePubMaster, _NS

HALF = 1.75
CAP = 0.70
DT = 0.2
RATE_STEP = 0.3 * DT


class _Line:
  def __init__(self, y):
    self.x = [0.0 + 1.5, 20.0 + 1.5, 100.0 + 1.5]    # 相机系 = 车体系 + 安装偏移 1.5
    self.y = [float(y)] * 3                          # 相机系 y 右正


def _model(half=HALF, probs=(0.9, 0.9), lane_change="off", outer_probs=(0.5, 0.5), outer_half=3 * HALF):
  m = _NS(action=_NS(desiredCurvature=0.01), roadEdges=[], meta=_NS(laneChangeState=lane_change),
          laneLines=[_Line(-outer_half), _Line(-half), _Line(half), _Line(outer_half)],
          laneLineProbs=[outer_probs[0], probs[0], probs[1], outer_probs[1]], laneLineStds=[0.1, 0.1, 0.1, 0.1],
          position=_Line(0.0))
  m.position.yStd = [0.1] * 3
  return m


class _SM:
  def __init__(self, model, car_state, radar, car_control, valid):
    self._data = {"modelV2": model, "carState": car_state, "radarTracks": radar, "carControl": car_control,
                  "extrinsicsCalibration": _NS(calStatus="calibrated", rpyCalib=[0.0, 0.0, 0.0]),
                  "deviceState": _NS(cpuUsagePercent=[10.0] * 8, memoryUsagePercent=50.0),
                  "procLog": _NS(mem=_NS(available=2 * 1024 ** 3)), "deviceMotion": _NS(inputsOK=True)}
    self.valid = {**valid, "extrinsicsCalibration": True}

  def update(self, timeout=0):
    pass

  def __getitem__(self, k):
    return self._data[k]


class _Params:
  def __init__(self, enabled):
    self._enabled = enabled

  def get_bool(self, key, block=False):
    return self._enabled

  def get(self, key, block=False, return_default=False):
    return None


def _points(radar, v_ego):
  # (dRel, yRel) 默认对向来车(对地 -10 m/s,接近速度 v_ego+10);(dRel, yRel, 对地速度) 显式指定
  return [_NS(dRel=e[0], yRel=e[1], vRel=(e[2] if len(e) > 2 else -10.0) - v_ego, trackId=i)
          for i, e in enumerate(radar)]


def _run(radar=(), *, frames=40, v_ego=20.0, enabled=True, lat_active=True, steering=False, lane_change="off",
         half=HALF, probs=(0.9, 0.9), camera=None, detector=None, outer_probs=(0.5, 0.5), outer_half=3 * HALF):
  """radar = [(dRel, yRel[, 对地速度])] 或 callable(帧号) -> 同样的列表。返回 (每帧 plan, 最后一帧 debug, 最后一帧 state)。"""
  car_state = _NS(vEgo=v_ego, leftBlindspot=False, rightBlindspot=False, steeringPressed=steering)
  points = _NS(points=_points(radar(0) if callable(radar) else radar, v_ego),
               errors=_NS(canError=False, radarUnavailableTemporary=False))
  sm = _SM(_model(half, probs, lane_change, outer_probs, outer_half), car_state, points, _NS(latActive=lat_active),
           {"modelV2": True, "carState": True, "radarTracks": True, "carControl": True})
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=sm, pm=pm, params=_Params(enabled), camera=camera, detector=detector)
  plans = []
  for i in range(frames):
    if callable(radar):
      points.points = _points(radar(i), v_ego)
    daemon.update(i * DT)
    plans.append([m for s, m in pm.sent if s == "lateralManeuverPlan"][-1])
  debug = [m for s, m in pm.sent if s == "eagleDebug"][-1].eagleDebug
  state = [m for s, m in pm.sent if s == "eagleState"][-1].eagleState
  return plans, debug, state


def _offset(plan):
  return plan.lateralManeuverPlan.desiredLaneOffset


# --- 偏移方向与大小 --------------------------------------------------------------

def test_right_target_pushes_left_by_pressure_times_cap():
  plans, debug, _ = _run([(30.0, -(HALF + 0.5 + 0.25))])   # 线距 0.25 → 压力 0.5
  assert plans[-1].valid
  assert debug.pressureRight == pytest.approx(0.5) and debug.pressureLeft == 0.0
  assert debug.offsetCap == pytest.approx(CAP)
  assert _offset(plans[-1]) == pytest.approx(0.5 * CAP)


def test_left_target_pushes_right():
  plans, debug, _ = _run([(30.0, HALF + 0.5 + 0.25)])
  assert plans[-1].valid
  assert debug.pressureLeft == pytest.approx(0.5)
  assert _offset(plans[-1]) == pytest.approx(-0.5 * CAP)


def test_equal_pressure_both_sides_centres_and_stays_valid():
  plans, _, _ = _run([(30.0, HALF + 0.75), (30.0, -(HALF + 0.75))])
  assert plans[-1].valid
  assert _offset(plans[-1]) == pytest.approx(0.0, abs=1e-6)


def test_offset_follows_pressure_difference():
  plans, _, _ = _run([(30.0, -(HALF + 0.5 + 0.25)), (30.0, HALF + 0.5 + 0.375)])   # 右 0.5 / 左 0.25
  assert _offset(plans[-1]) == pytest.approx(0.25 * CAP)


def test_intruding_target_saturates_pressure_but_never_exceeds_cap():
  plans, debug, _ = _run([(30.0, -(HALF + 0.1))])           # 中心在线外 0.1m,半宽 0.5 → 车身越线 0.4m
  assert debug.pressureRight == 1.0
  assert _offset(plans[-1]) == pytest.approx(CAP)


def test_narrow_lane_gives_zero_offset():
  plans, debug, _ = _run([(30.0, -(1.0 + 0.5 + 0.1))], half=1.0)   # 半宽 1.0 - 0.9 - 0.15 < 0
  assert debug.offsetCap == 0.0
  assert _offset(plans[-1]) == 0.0


def test_cap_scales_with_lane_width():
  plans, debug, _ = _run([(30.0, -2.5)], half=2.0)   # 线距 0 → 压力 1
  assert debug.offsetCap == pytest.approx(0.95)
  assert _offset(plans[-1]) == pytest.approx(0.95)


def test_target_inside_own_lane_is_not_an_adjacent_target():
  plans, debug, _ = _run([(30.0, 0.3)])
  assert debug.pressureLeft == 0.0 and debug.pressureRight == 0.0
  assert not plans[-1].valid


def test_far_target_is_ignored_and_range_edge_counts():
  _, far, _ = _run([(61.0, -(HALF + 0.75))])
  _, edge, _ = _run([(59.0, -(HALF + 0.75))])
  assert far.pressureRight == 0.0
  assert edge.pressureRight == pytest.approx(0.5)


# --- 到达时间窗口(默认 4.0s):到达时间 = dRel / 接近速度 -------------------------------

RIGHT_Y = -(HALF + 0.75)   # 线距 0.25 → 压力 0.5(机动车)


def test_oncoming_fast_car_triggers_far_before_a_slow_one_at_the_same_distance():
  _, fast, _ = _run([(55.0, RIGHT_Y)])             # 接近速度 30 → 1.8s
  _, slow, _ = _run([(55.0, RIGHT_Y, 15.0)])       # 接近速度 5 → 11s
  assert fast.pressureRight == pytest.approx(0.5)
  assert slow.pressureRight == 0.0


def test_arrival_window_edge_counts():
  _, edge, _ = _run([(20.0, RIGHT_Y, 15.0)])       # 20 / 5 = 4.0s
  _, late, _ = _run([(20.5, RIGHT_Y, 15.0)])       # 4.1s
  assert edge.pressureRight == pytest.approx(0.5)
  assert late.pressureRight == 0.0


def test_receding_or_matching_speed_target_is_not_counted():
  _, receding, _ = _run([(10.0, RIGHT_Y, 25.0)])   # 比自车快,接近速度 -5
  _, matching, _ = _run([(10.0, RIGHT_Y, 20.0)])   # 接近速度 0
  assert receding.pressureRight == 0.0 and matching.pressureRight == 0.0


def test_vision_only_target_assumes_slow_same_direction_traffic():
  # 速度未知:接近速度 = max(自车速度 - 5 m/s, 1 m/s 下限);雷达实测则 = -vRel
  assert approach_speed(None, 20.0) == 15.0
  assert approach_speed(None, 5.5) == 1.0
  assert approach_speed(-30.0, 20.0) == 30.0


def test_vision_only_target_uses_the_assumed_approach_speed_for_the_window():
  def vision(d_rel):   # 自车 8 m/s → 假设接近速度 3 m/s:窗口内最远 12m
    return _run(camera=_FakeCamera([ROI]), detector=_FakeDetector([_box_at(d_rel, -(HALF + 0.3 + 0.5), "person")]),
                v_ego=8.0, frames=1)[1]
  assert vision(10.0).pressureRight > 0.0
  assert vision(14.0).pressureRight == 0.0


# --- 并行保持:目标离开前向视野后按推算保留压力 ----------------------------------------
# 目标对地 15 m/s、自车 20 m/s → 接近速度 5 m/s。dRel 从 18m 起收缩,第 15 帧(dRel 3m)是最后可见帧。

VANISH_FRAME = 16


def _overtaken(vanish_frame=VANISH_FRAME, ground_speed=15.0, start=18.0):
  closing = 20.0 - ground_speed
  return lambda i: [(start - closing * DT * i, RIGHT_Y, ground_speed)] if i < vanish_frame else []


def _frames_after_vanish(seconds):
  return VANISH_FRAME + round(seconds / DT)


def test_pressure_is_held_after_target_leaves_view():
  plans, debug, _ = _run(_overtaken(), frames=_frames_after_vanish(1.0))
  assert debug.pressureRight == pytest.approx(0.5)      # 推算 dRel 3 → -2,还没超过
  assert debug.holdingTargets == 1
  assert plans[-1].valid and _offset(plans[-1]) == pytest.approx(0.5 * CAP)


def test_hold_releases_once_ego_has_passed_target_plus_length_margin():
  # 最后可见 dRel 3m → -10m(车长余量 10m)需 13/5 = 2.6s
  _, held, _ = _run(_overtaken(), frames=_frames_after_vanish(2.2))
  _, released, _ = _run(_overtaken(), frames=_frames_after_vanish(3.0))
  assert held.pressureRight == pytest.approx(0.5) and held.holdingTargets == 1
  assert released.pressureRight == 0.0 and released.holdingTargets == 0


def test_hold_is_capped_by_max_duration():
  # 接近速度 1.5 m/s:推算超过要 8s 以上,最长时限 5s 先到
  slow = _overtaken(vanish_frame=VANISH_FRAME, ground_speed=18.5, start=7.0)
  _, held, _ = _run(slow, frames=_frames_after_vanish(4.5))
  _, released, _ = _run(slow, frames=_frames_after_vanish(5.4))
  assert held.pressureRight == pytest.approx(0.5)
  assert released.pressureRight == 0.0


def test_visible_target_moving_away_laterally_is_not_held():
  def moves_out(i):
    return [(10.0, RIGHT_Y if i < 10 else -(HALF + 1.5), 15.0)]   # 线距 1.0 → 压力 0
  _, debug, _ = _run(moves_out, frames=14)
  assert debug.pressureRight == 0.0 and debug.holdingTargets == 0


def test_unconfirmed_static_radar_point_does_not_trigger():
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  guardrail = _NS(points=[_NS(dRel=30.0, yRel=-2.5, vRel=-20.0, trackId=1)],
                  errors=_NS(canError=False, radarUnavailableTemporary=False))
  sm = _SM(_model(), car_state, guardrail, _NS(latActive=True),
           {"modelV2": True, "carState": True, "radarTracks": True, "carControl": True})
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=sm, pm=pm, params=_Params(True))
  daemon.update(0.0)
  debug = [m for s, m in pm.sent if s == "eagleDebug"][-1].eagleDebug
  assert debug.pressureRight == 0.0


def test_vulnerable_road_user_triggers_at_larger_line_distance_than_vehicle():
  # 同一线距 0.5m:行人(触发线距 1.0)压力 0.5;机动车(触发线距 0.5)压力 0
  def vision(cls, half_width):
    camera = _FakeCamera([ROI] * 40)
    detector = _FakeDetector([_box_at(30.0, -(HALF + half_width + 0.5), cls)])
    return _run(camera=camera, detector=detector, frames=1)[1]
  assert vision("person", 0.3).pressureRight == pytest.approx(0.5, abs=0.05)
  assert vision("car", 0.9).pressureRight == pytest.approx(0.0, abs=0.05)


# --- 可信度与生效门 ---------------------------------------------------------------

@pytest.mark.parametrize("probs", [(0.1, 0.9), (0.9, 0.1)])
def test_either_lane_line_untrusted_disables(probs):
  plans, debug, _ = _run([(30.0, -(HALF + 0.75))], probs=probs)
  assert not plans[-1].valid
  assert debug.inactiveReason == "lane_untrusted"
  assert _offset(plans[-1]) == 0.0


@pytest.mark.parametrize("kwargs, reason", [
  ({"enabled": False}, "disabled"),
  ({"lat_active": False}, "lat_inactive"),
  ({"steering": True}, "steering_pressed"),
  ({"lane_change": "preLaneChange"}, "lane_change"),
  ({"v_ego": 4.0}, "speed"),     # < 15 km/h
  ({"v_ego": 34.0}, "speed"),
])
def test_effect_gates(kwargs, reason):
  plans, debug, _ = _run([(30.0, -(HALF + 0.75))], **kwargs)
  assert not plans[-1].valid
  assert debug.inactiveReason == reason
  assert _offset(plans[-1]) == 0.0


def test_gates_open_reports_empty_reason():
  _, debug, state = _run([(30.0, -(HALF + 0.75))])
  assert debug.inactiveReason == "" and state.inactiveReason == ""


# --- 速率限制 ---------------------------------------------------------------------

def test_offset_builds_at_rate_limit():
  plans, _, _ = _run([(30.0, -(HALF + 0.5 + 0.25))], frames=3)
  assert [_offset(p) for p in plans] == pytest.approx([RATE_STEP, 2 * RATE_STEP, 3 * RATE_STEP])


def test_offset_released_at_rate_limit_and_stays_valid_until_zero():
  # 前 40 帧有目标贴线,之后目标侧移走(仍可见、压力 0):偏移按速率回 0,回 0 前 plan 保持 valid
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=30.0, yRel=-(HALF + 0.75), vRel=-30.0, trackId=1)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  sm = _SM(_model(), car_state, radar, _NS(latActive=True),
           {"modelV2": True, "carState": True, "radarTracks": True, "carControl": True})
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=sm, pm=pm, params=_Params(True))
  for i in range(40):
    daemon.update(i * DT)
  held = _offset(pm.sent[-1][1])
  radar.points = [_NS(dRel=30.0, yRel=-(HALF + 3.0), vRel=-30.0, trackId=1)]
  daemon.update(40 * DT)
  after = pm.sent[-1][1]
  assert _offset(after) == pytest.approx(held - RATE_STEP)
  assert after.valid
  for i in range(41, 60):
    daemon.update(i * DT)
  assert _offset(pm.sent[-1][1]) == 0.0 and not pm.sent[-1][1].valid


def test_gate_closing_ramps_published_offset_to_zero_but_invalid():
  plans, _, _ = _run([(30.0, -(HALF + 0.75))], lat_active=False, frames=5)
  assert all(not p.valid and _offset(p) == 0.0 for p in plans)


# --- 遥测 -------------------------------------------------------------------------

def test_target_rows_carry_line_distance_and_pressure():
  _, debug, state = _run([(30.0, -(HALF + 0.5 + 0.25))], frames=1)
  row = debug.targets[0]
  assert row.lineDistance == pytest.approx(0.25) and row.pressure == pytest.approx(0.5)
  assert state.pressureRight == pytest.approx(0.5)
  assert state.offsetCap == pytest.approx(CAP)
  assert debug.laneOffsetTarget == pytest.approx(RATE_STEP)


# --- 间隙不足 --------------------------------------------------------------------
# 窄车道(半宽 1.2):贴线上限 0.15。侵入 0.4m 的目标在偏移目标下剩余间隙 = -0.4 + (1.2-0.9) + 0.15 = 0.05 < 余量 0.15。

def _gap_insufficient(radar, **kw):
  return _run(radar, half=1.2, **kw)[0][-1].lateralManeuverPlan.gapInsufficient


@pytest.mark.parametrize("y_rel", [-1.3, 1.3])
def test_deeply_intruding_target_sets_gap_insufficient_on_either_side(y_rel):
  assert _gap_insufficient([(30.0, y_rel)])


def test_barely_intruding_target_leaves_enough_gap():
  assert not _gap_insufficient([(30.0, -1.45)])      # 侵入 0.25 → 间隙 0.2 ≥ 余量


def test_non_intruding_target_does_not_set_gap_insufficient():
  assert not _gap_insufficient([(30.0, -1.8)])       # 线距 +0.1


def test_wide_lane_leaves_enough_gap_even_when_intruding():
  assert not _run([(30.0, -(HALF + 0.1))])[0][-1].lateralManeuverPlan.gapInsufficient


def test_gap_insufficient_not_set_when_avoidance_inactive():
  assert not _gap_insufficient([(30.0, -1.3)], steering=True)


# --- 变道清空(目标车道经车道线确认) ----------------------------------------------------
# 目标车道:本车道线与再外一条线之间。外侧线默认概率 0.5 → 不可信 → 按本车道同宽推定。
# 目标例:左邻道中心 yRel = 3.5。对地速度为第三项;v_ego=20。

def _clear(radar, **kw):
  state = _run(radar, frames=1, **kw)[2]
  return str(state.changeClearLeftState), str(state.changeClearRightState)


def test_slow_lead_in_own_lane_does_not_block_left_change():
  assert _clear([(25.0, 0.2, 5.0)]) == ("clear", "clear")


def test_near_zone_target_in_target_lane_blocks_that_side_only():
  assert _clear([(5.0, 3.5, 30.0)]) == ("blocked", "clear")
  assert _clear([(5.0, -3.5, 30.0)]) == ("clear", "blocked")


def test_time_projection_blocks_slow_and_passes_far_fast():
  assert _clear([(20.0, 3.5, 5.0)])[0] == "blocked"      # 20 + 5*4 = 40 <= 20*3
  assert _clear([(30.0, 3.5, 25.0)])[0] == "clear"       # 30 + 25*4 = 130 > 60


def test_slow_target_beyond_60m_still_blocks_at_highway_speed():
  assert _clear([(70.0, 3.5, 5.0)], v_ego=33.0)[0] == "blocked"   # 70 + 5*4 = 90 <= 33*3


def test_unknown_speed_target_in_target_lane_blocks():
  vision = {"camera": _FakeCamera([ROI]), "detector": _FakeDetector([_box_at(30.0, 3.5, "car")])}
  assert _clear([], **vision)[0] == "blocked"


def test_target_two_lanes_over_is_not_in_target_lane():
  assert _clear([(20.0, 6.5, 5.0)]) == ("clear", "clear")


def test_untrusted_outer_line_infers_same_width_target_lane():
  assert _clear([(20.0, 5.0, 5.0)])[0] == "blocked"      # 推定 [1.75, 5.25]


def test_trusted_outer_line_is_used_instead_of_inference():
  narrow = {"outer_probs": (0.9, 0.9), "outer_half": HALF + 2.5}   # 邻道只有 2.5m 宽 → [1.75, 4.25]
  assert _clear([(20.0, 4.8, 5.0)], **narrow)[0] == "clear"
  assert _clear([(20.0, 3.5, 5.0)], **narrow)[0] == "blocked"


def test_untrusted_ego_lane_line_makes_clear_unknown_but_legacy_bool_does_not_block():
  _, debug, state = _run([(5.0, 3.5, 30.0)], frames=1, probs=(0.9, 0.3))
  assert str(state.changeClearLeftState) == "unknown" and str(state.changeClearRightState) == "unknown"
  assert str(debug.changeClearLeftState) == "unknown" and str(debug.changeClearRightState) == "unknown"
  assert state.changeClearLeft is True and state.changeClearRight is True
