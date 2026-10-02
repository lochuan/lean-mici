"""切口 ①:EagleDaemon.update——邻道目标 → 侧向压力 → 车道内偏移(CONTEXT.md)。

断言只看发布出去的消息字段(lateralManeuverPlan / eagleDebug / eagleState)。
车体系 y 左正:左邻目标 yRel > 0。默认 3.5m 车道(半宽 1.75)、自车半宽 0.9、
贴线余量 0.15 → 贴线上限 0.70m。无类别雷达点按机动车处理(触发线距 0.5m、半宽 0.5m)。
压力例:右侧点 yRel = -(1.75 + 0.5 + 线距)。
"""
import pytest

from openpilot.selfdrive.eagled.eagled import EagleDaemon
from openpilot.selfdrive.eagled.tests.test_daemon_fusion import ROI, _box_at, _FakeCamera, _FakeDetector, _FakePubMaster, _NS

HALF = 1.75
CAP = 0.70
DT = 0.2
RATE_STEP = 0.3 * DT


class _Line:
  def __init__(self, y):
    self.x = [0.0 + 1.5, 20.0 + 1.5, 100.0 + 1.5]    # 相机系 = 车体系 + 安装偏移 1.5
    self.y = [float(y)] * 3                          # 相机系 y 右正


def _model(half=HALF, probs=(0.9, 0.9), lane_change="off"):
  m = _NS(action=_NS(desiredCurvature=0.01), roadEdges=[], meta=_NS(laneChangeState=lane_change),
          laneLines=[_Line(-half), _Line(-half), _Line(half), _Line(half)],
          laneLineProbs=[0.5, probs[0], probs[1], 0.5], laneLineStds=[0.5, 0.1, 0.1, 0.5],
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


def _run(radar=(), *, frames=40, v_ego=20.0, enabled=True, lat_active=True, steering=False, lane_change="off",
         half=HALF, probs=(0.9, 0.9), camera=None, detector=None):
  """radar = [(dRel, yRel)],都是对地 15m/s 的移动点。返回 (每帧 plan, 最后一帧 debug, 最后一帧 state)。"""
  car_state = _NS(vEgo=v_ego, leftBlindspot=False, rightBlindspot=False, steeringPressed=steering)
  points = _NS(points=[_NS(dRel=d, yRel=y, vRel=15.0 - v_ego, trackId=i) for i, (d, y) in enumerate(radar)],
               errors=_NS(canError=False, radarUnavailableTemporary=False))
  sm = _SM(_model(half, probs, lane_change), car_state, points, _NS(latActive=lat_active),
           {"modelV2": True, "carState": True, "radarTracks": True, "carControl": True})
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=sm, pm=pm, params=_Params(enabled), camera=camera, detector=detector)
  plans = []
  for i in range(frames):
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
  # 前 40 帧有目标,之后目标消失:偏移按速率回 0,回 0 前 plan 保持 valid
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=30.0, yRel=-(HALF + 0.75), vRel=-5.0, trackId=1)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  sm = _SM(_model(), car_state, radar, _NS(latActive=True),
           {"modelV2": True, "carState": True, "radarTracks": True, "carControl": True})
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=sm, pm=pm, params=_Params(True))
  for i in range(40):
    daemon.update(i * DT)
  held = _offset(pm.sent[-1][1])
  radar.points = []
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
