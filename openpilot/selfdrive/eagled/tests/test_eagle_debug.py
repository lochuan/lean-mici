"""eagleDebug publishing tests (daemon level, stub camera + detector)."""

import pytest

from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.eagled import EagleDaemon
from openpilot.selfdrive.eagled.tests.test_daemon_fusion import (MODEL_CURVATURE, ROI, _FakeCamera, _FakeDetector,
                                                                 _FakeParams, _FakePubMaster, _FakeSubMaster, _NS,
                                                                 _box_at, _daemon)



def _debug_msgs(pm):
  return [msg for service, msg in pm.sent if service == 'eagleDebug']


def test_services_entry_is_5hz_and_logged():
  """eagleDebug 必须落 rlog:2026-09-25 路测只统计到"避让触发 57 次",
  却无从复盘每次的目标/方向/门控原因(should_log=False 的代价)。
  5Hz、每帧 KB 级,落盘量可忽略。"""
  svc = SERVICE_LIST["eagleDebug"]
  assert svc.frequency == 5.
  assert svc.should_log is True


def test_debug_message_sent_every_frame_even_invalid():
  daemon, pm = _daemon(radar_points=[])
  daemon.update(0.0)
  daemon.update(C.DT_5HZ)
  debug = [(s, m) for s, m in pm.sent if s == 'eagleDebug']
  plans = [(s, m) for s, m in pm.sent if s == 'lateralManeuverPlan']
  assert len(debug) == len(plans) == 2   # every-frame publish, both services
  assert all(m.valid is True for _, m in debug)   # observation is always valid
  assert debug[-1][1].eagleDebug.valid is False   # planner validity lives in the struct


def test_debug_targets_radar_and_vision_with_matching_pair_ids():
  # One radar point and one YOLO box at the same spot: both carry matched=True
  # and the same non-zero pairId; nothing else gets a pair.
  person = _box_at(20.0, -1.8, cls="person", conf=0.8)
  far_car = _box_at(60.0, 0.0, cls="car", conf=0.9)  # outside the planner gate
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[person, far_car]),
                       radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].eagleDebug

  radar_t = [t for t in dbg.targets if not t.vision]
  vision_t = [t for t in dbg.targets if t.vision]
  assert len(radar_t) == 1 and len(vision_t) == 2

  # radar side: vision-confirmed as person -> the vision class and the VRU
  # weight the planner actually used (not the old hardcoded vehicle default);
  # has vRel, matched to pair 1
  rt = [t for t in radar_t if t.dRel == pytest.approx(20.0)][0]
  assert rt.cls == "person" and rt.vision is False and rt.matched is True
  assert rt.pairId == 1 and rt.inGate is True
  assert rt.weight == pytest.approx(C.VRU_WEIGHT)

  # vision side: person matched (pair 1); far car unmatched (pairId 0, not in gate)
  vt = [t for t in vision_t if t.inGate][0]
  assert vt.cls == "person" and vt.vision is True and vt.matched is True
  assert vt.pairId == 1 and vt.conf == pytest.approx(0.8)
  assert vt.weight == pytest.approx(C.VRU_WEIGHT)
  out = [t for t in vision_t if not t.inGate][0]
  assert out.cls == "car" and out.matched is False and out.pairId == 0 and out.inGate is False

  assert dbg.nRadar == 1 and dbg.nVision == 2 and dbg.nAssociated == 1
  assert dbg.vEgo == pytest.approx(20.0)


def test_debug_radar_weight_matches_planner_weight_when_vision_confirmed():
  """A vision-confirmed VRU radar point is planned with the VRU weight
  (fuse_targets takes the vision class) — the debug row must report THAT
  weight, not the hardcoded vehicle default. Telemetry that disagrees with the
  action is how bugs stay invisible in shadow logs."""
  person = _box_at(20.0, -1.8, cls="person", conf=0.8)
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[person]),
                       radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].eagleDebug
  rt = [t for t in dbg.targets if not t.vision][0]
  assert rt.matched is True
  assert rt.cls == "person"
  assert rt.weight == pytest.approx(C.VRU_WEIGHT)


def test_debug_counts_and_gate_flags_radar_only():
  # Radar-only degrade path still publishes debug with correct counts/gates.
  near, far = (8.0, -1.8), (80.0, 0.0)
  daemon, pm = _daemon(camera=_FakeCamera(), radar_points=[near, far])
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert dbg.nRadar == 2 and dbg.nVision == 0 and dbg.nAssociated == 0
  gates = {bool(t.inGate) for t in dbg.targets}
  assert gates == {True, False}
  assert all(not t.vision for t in dbg.targets)
  assert all(t.pairId == 0 and not t.matched for t in dbg.targets)
  assert dbg.valid is False  # planner not yet active (hysteresis)


def test_debug_carries_planner_state():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert dbg.valid is True and dbg.active is True
  assert dbg.direction == 1  # target on the right -> avoid left
  assert dbg.yDes > 0.0
  assert dbg.maxOffset == pytest.approx(C.MAX_OFFSET_FREE)
  assert dbg.edgeClearance == 999.0  # no road edges -> inf sent as 999.0


def test_debug_bsm_flags_from_car_state():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       radar_points=[])
  # patch blindspot flags after construction
  daemon.sm._data["carState"].leftBlindspot = False
  daemon.sm._data["carState"].rightBlindspot = True
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert dbg.bsmLeft is False and dbg.bsmRight is True


def test_debug_carries_radar_error_flags():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       radar_points=[])
  daemon.sm._data["radarTracks"].errors.canError = True
  daemon.sm._data["radarTracks"].errors.radarUnavailableTemporary = True
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert dbg.canError is True and dbg.radarUnavailable is True


# --- eagleState: the formal perception picture -------------------------------------


def _state_msgs(pm):
  return [msg for service, msg in pm.sent if service == 'eagleState']


def test_state_service_is_5hz_and_not_logged():
  svc = SERVICE_LIST["eagleState"]
  assert svc.frequency == 5.
  assert svc.should_log is False


def test_state_published_every_frame_with_ingate_targets_only():
  # One in-gate radar+vision pair and one out-of-gate far car: eagleState
  # carries the in-gate row(s) only — the picture, not the raw telemetry.
  person = _box_at(20.0, -1.8, cls="person", conf=0.8)
  far_car = _box_at(60.0, 0.0, cls="car", conf=0.9)  # outside the planner gate
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[person, far_car]),
                       radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  daemon.update(C.DT_5HZ)
  states = _state_msgs(pm)
  assert len(states) == 2                       # every frame, perception always on
  assert all(m.valid is True for m in states)   # observation, never a plan
  st = states[-1].eagleState
  assert st.nRadar == 1 and st.nVision == 2 and st.nAssociated == 1
  assert st.vEgo == pytest.approx(20.0)
  assert all(t.inGate for t in st.targets)      # out-of-gate far car filtered out
  assert len(st.targets) == 2                   # the radar point + its matched vision row
  assert st.targets[0].vision is False and st.targets[1].vision is True


def test_state_publishes_even_when_avoidance_disabled():
  # AvoidanceEnabled gates the lateralManeuverPlan actuation and the vision
  # chain; with the param off the streams keep flowing, radar-only.
  daemon, pm = _daemon(enabled=False, radar_points=[(8.0, -1.8)])
  daemon.update(0.0)
  plans = [msg for service, msg in pm.sent if service == "lateralManeuverPlan"]
  assert plans[-1].valid is False
  assert len(_state_msgs(pm)) == 1
  assert len(_debug_msgs(pm)) == 1


def test_state_carries_bsm_and_radar_health():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       radar_points=[])
  daemon.sm._data["carState"].rightBlindspot = True
  daemon.sm._data["radarTracks"].errors.canError = True
  daemon.update(0.0)
  st = _state_msgs(pm)[-1].eagleState
  assert st.bsmLeft is False and st.bsmRight is True
  assert st.canError is True and st.radarUnavailable is False


# --- C2/C7: lane geometry flags + lane labels -------------------------------------


def _lane_aware_model_v2():
  """带车道几何的 modelV2 桩:标准 3.5m 车道,双侧边界置信达标。"""
  x = [5.0, 20.0, 40.0]
  return _NS(
    action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[], meta=_NS(laneChangeState="off"),
    laneLines=[_NS(x=x, y=[-1.75] * 3), _NS(x=x, y=[-1.75] * 3),
               _NS(x=x, y=[1.75] * 3), _NS(x=x, y=[1.75] * 3)],
    laneLineProbs=[0.5, 0.9, 0.9, 0.5],
    laneLineStds=[0.5, 0.1, 0.1, 0.5],
    position=_NS(x=x, y=[0.0] * 3, yStd=[0.1] * 3),
  )


def test_streams_publish_lane_geometry_flags_and_lane_labels():
  # 邻道压线车（yRel=2.6,固定带上界 2.5 外）:tier 1 按车道线判进,lane=-1,
  # 双侧置信标志透传到两条流。
  model_v2 = _lane_aware_model_v2()
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=20.0, yRel=2.6, vRel=0.0)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams(enabled=True),
                       camera=_FakeCamera(frames=[ROI]),
                       detector=_FakeDetector(detections=[_box_at(20.0, 2.6, cls="car")]))
  daemon.update(0.0)
  st = _state_msgs(pm)[-1].eagleState
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert st.laneLeftValid is True and st.laneRightValid is True
  assert dbg.laneLeftValid is True and dbg.laneRightValid is True
  assert len(st.targets) == 2                      # 雷达行 + 视觉行,都已 tier 1 判进
  assert st.targets[0].lane == -1 and st.targets[1].lane == -1
  # 遥测与行动同源:这个固定带外的目标真的进了计划（tier 1 侵入语义生效）
  assert dbg.targets[0].inGate is True and dbg.targets[0].lane == -1


def test_streams_publish_false_flags_when_geometry_unavailable():
  # daemon 测试桩形态的 modelV2（无车道线字段）:geo=None,双侧 False,lane 走
  # 固定带符号。
  daemon, pm = _daemon(radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  st = _state_msgs(pm)[-1].eagleState
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert st.laneLeftValid is False and st.laneRightValid is False
  assert dbg.laneLeftValid is False and dbg.laneRightValid is False
  assert st.targets[0].lane == 1                   # 固定带:右侧目标 lane=+1


# --- C9: budget + sideLead publication --------------------------------------------


def test_budgets_published_and_flow_into_the_plan():
  # 左侧邻道目标(|yRel|=2.6,无视觉类别 -> 默认半宽 0.5)
  # -> budget_left = 2.6-0.5-0.9-0.3 = 0.9 > 0.35,本帧不压偏置;
  # 右侧威胁(yRel=-1.8,vehicle 权重)desire = 0.5*0.6*(1-20/50) = 0.18 全额放行。
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=20.0, yRel=-1.8, vRel=0.0),      # 右侧威胁(带内)
                      _NS(dRel=18.0, yRel=2.6, vRel=0.0)],       # 左侧邻道目标(带外,进预算)
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams(enabled=True))
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)

  st = _state_msgs(pm)[-1].eagleState
  dbg = _debug_msgs(pm)[-1].eagleDebug
  assert st.budgetLeft == pytest.approx(0.9)
  # 右侧威胁(|yRel|=1.8,默认半宽 0.5)也约束右侧预算: 1.8-0.5-0.9-0.3 = 0.1
  assert st.budgetRight == pytest.approx(0.1)
  assert dbg.budgetLeft == pytest.approx(0.9) and dbg.budgetRight == pytest.approx(0.1)
  # 变道清空:两个目标都是同速远车(≥18m,vLead=vEgo),时间投影都放行
  assert st.changeClearLeft is True and st.changeClearRight is True
  assert dbg.changeClearLeft is True and dbg.changeClearRight is True
  assert st.sideLeadLeft.valid is True and st.sideLeadLeft.cls == ""    # 无视觉类别 -> 默认半宽
  assert st.sideLeadLeft.edgeDist == pytest.approx(2.6 - 0.5)
  assert st.sideLeadLeft.vRel == pytest.approx(0.0)
  # 右侧约束 lead 就是那个威胁本身
  assert st.sideLeadRight.valid is True and st.sideLeadRight.edgeDist == pytest.approx(1.3)
  # 预算 0.9 > desire 0.18:向左偏置全额放行(右预算 0.1 管的是向右偏,不参与)。
  # 首帧低通 alpha = 0.2/0.7
  alpha = C.DT_5HZ / (C.LOWPASS_TAU_S + C.DT_5HZ)
  plans = [msg for service, msg in pm.sent if service == "lateralManeuverPlan"]
  assert plans[-1].lateralManeuverPlan.desiredCurvature == pytest.approx(
    MODEL_CURVATURE + 2.0 * alpha * 0.18 / C.L_LOOKAHEAD ** 2)


def test_bsm_maps_to_zero_budget_without_a_lead():
  # BSM 左 -> budget_left 0 + 不清空,lead 无可指;右侧威胁的计划仍 valid 但偏置 0
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=True, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=20.0, yRel=-1.8, vRel=0.0)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams(enabled=True))
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  st = _state_msgs(pm)[-1].eagleState
  assert st.budgetLeft == 0.0 and st.sideLeadLeft.valid is False
  assert st.changeClearLeft is False          # BSM 侧不清空(变道门消费)
  assert st.changeClearRight is True          # 右侧同速远车投影放行
  plans = [msg for service, msg in pm.sent if service == "lateralManeuverPlan"]
  assert plans[-1].valid is True   # 滞回按目标存在性,预算 0 只消偏置
  assert plans[-1].lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)
