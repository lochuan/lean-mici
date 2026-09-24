"""Daemon-level fusion tests: stub camera + detector wired into EagleDaemon."""

import numpy as np
import pytest

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.eagled import EagleDaemon, PARAMS_REFRESH_PERIOD
from openpilot.selfdrive.eagled.perception import PerceptionCore
from openpilot.selfdrive.eagled.projection import RoiMeta

MODEL_CURVATURE = 0.012

# Synthetic wide-camera intrinsics (full frame 1344x760, focal 425.25).
FX = FY = 425.25
CX, CY = 672.0, 380.0

ROI = (np.zeros((384, 640, 3), np.uint8), RoiMeta(1.0, 1.0, 0.0))


class _NS:
  def __init__(self, **kwargs):
    self.__dict__.update(kwargs)


class _FakePubMaster:
  def __init__(self):
    self.sent = []

  def send(self, service, msg):
    self.sent.append((service, msg))


class _FakeParams:
  def __init__(self, enabled=True):
    self._enabled = enabled

  def get_bool(self, key, block=False):
    return self._enabled

  def get(self, key, block=False, return_default=False):
    return None


class _FakeSubMaster:
  def __init__(self, model_v2, car_state, radar, valid=None,
               cal_status="calibrated", rpy=(0.0, 0.0, 0.0)):
    # Default calibration message is calibrated with zero rpy: identical to the
    # pre-calibration mount constants (C.CAMERA_PITCH / C.CAMERA_YAW are 0.0),
    # so existing tests keep their exact expected projections.
    self._data = {"modelV2": model_v2, "carState": car_state, "radarTracks": radar,
                  "extrinsicsCalibration": _NS(calStatus=cal_status, rpyCalib=list(rpy)),
                  # health services: healthy defaults (DeviceHealth degrades to these)
                  "deviceState": _NS(cpuUsagePercent=[10.0] * 8, memoryUsagePercent=50.0),
                  "procLog": _NS(mem=_NS(available=2 * 1024 ** 3)),
                  "deviceMotion": _NS(inputsOK=True)}
    self.valid = valid if valid is not None else dict.fromkeys(self._data, True)
    self.valid.setdefault("extrinsicsCalibration", True)

  def update(self, timeout=0):
    pass

  def __getitem__(self, service):
    return self._data[service]


class _FakeCamera:
  """Serves pre-canned ROI frames; intrinsics match the synthetic projection."""

  def __init__(self, frames=()):
    self._frames = list(frames)
    self.intrinsics = (FX, FY, CX, CY)
    self.frame_size = (1344, 760)

  def frame(self, horizon_row=None):
    return self._frames.pop(0) if self._frames else None


class _FakeDetector:
  def __init__(self, detections=()):
    self._detections = list(detections)
    self.calls = 0

  def infer(self, roi, now=None):
    self.calls += 1
    return self._detections


def _box_at(d_rel, y_rel, cls="person", conf=0.9):
  """ROI box (1:1 scale) whose bottom-centre projects to (d_rel, y_rel).

  The height is chosen so box-height ranging returns d_rel in the BUMPER frame
  (camera-frame range d_rel + CAMERA_TO_FRONT, i.e. h_px = fy*H/d_cam) — the
  same frame the ground-plane projection and the radar use. The bearing matcher
  re-distances unmatched detections by box height, so a synthetic box whose
  height disagrees with its ground distance would silently move the fused
  target (and trip the secondary range gate).
  """
  d_cam = d_rel + C.CAMERA_TO_FRONT
  v = CY + FY * C.CAMERA_HEIGHT / d_cam
  u = CX - FX * y_rel / d_cam
  h_px = FY * C.CLASS_HEIGHTS_M[cls] / d_cam
  return {"x1": u - 10.0, "y1": v - h_px, "x2": u + 10.0, "y2": v, "cls": cls, "conf": conf}


def _daemon(*, camera=None, detector=None, camera_factory=None, radar_points=(), enabled=True,
            model_valid=True):
  # meta.laneChangeState="off" mirrors the real modelV2 message (log.capnp
  # MetaData): the daemon reads it for the lane-change suppression gate.
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=d, yRel=y, vRel=0.0) for d, y in radar_points],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  kwargs = {}
  if camera is not None:
    kwargs["camera"] = camera
  if camera_factory is not None:
    kwargs["camera_factory"] = camera_factory
  if detector is not None:
    kwargs["detector"] = detector
  pm = _FakePubMaster()
  sm = _FakeSubMaster(model_v2, car_state, radar,
                      valid={"modelV2": model_valid, "carState": True, "radarTracks": True})
  daemon = EagleDaemon(sm=sm, pm=pm, params=_FakeParams(enabled=enabled), **kwargs)
  return daemon, pm


def _bias_curv(offset):
  return 2.0 * offset / C.L_LOOKAHEAD ** 2


def _first_frame_bias(offset):
  # FirstOrderFilter(0, tau=0.5, dt=0.2): first update scales by dt/(tau+dt).
  alpha = C.DT_5HZ / (C.LOWPASS_TAU_S + C.DT_5HZ)
  return _bias_curv(offset) * alpha


def _expected_offset(d_rel, weight):
  return min(C.MAX_OFFSET_FREE, C.K_GAIN * weight * (1.0 - d_rel / C.D_MAX))


# --- fusion chain ----------------------------------------------------------------

def test_daemon_vru_detection_gets_vru_weight():
  # Radar sees nothing; YOLO spots a person on the right -> VRU weight drives the bias.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.update(0.0)                    # enter hysteresis
  daemon.update(C.ENTER_HOLD_S + 0.01)  # active
  assert pm.sent[-1][1].valid is True
  expected = MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VRU_WEIGHT))
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(expected, rel=1e-6)


def test_daemon_vru_weight_exceeds_vehicle_weight():
  frames = [ROI] * 2
  person, _ = _daemon(camera=_FakeCamera(frames),
                      detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  car, _ = _daemon(camera=_FakeCamera(frames),
                   detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="car")]))
  for d in (person, car):
    d.update(0.0)
    d.update(C.ENTER_HOLD_S + 0.01)
  person_curv = person.pm.sent[-1][1].lateralManeuverPlan.desiredCurvature
  car_curv = car.pm.sent[-1][1].lateralManeuverPlan.desiredCurvature
  assert person_curv > car_curv > MODEL_CURVATURE
  assert car_curv == pytest.approx(MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VEHICLE_WEIGHT)), rel=1e-6)


def test_daemon_associated_detection_not_double_counted():
  # Radar point and YOLO box at the same spot: the radar point absorbs the
  # detection (one target, not two). Task 5: the absorbed point takes the
  # vision class weight, so a radar point matched to a person box is a VRU.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]),
                       radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is True
  expected = MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VRU_WEIGHT))
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(expected, rel=1e-6)


def test_daemon_confirms_static_radar_across_reiteration():
  """设备形态回归钉:radar.points 每次迭代产出新包装对象(模拟 pycapnp)。

  associate 在内部物化一份点对象,fuse_targets 再迭代 radar.points 拿到的是
  全新等价对象 —— 确认键必须跟 trackId 走。键逻辑退回 id() 时,静止点会被
  当成未确认的杂波丢掉,本测试变红。"""
  class _ReiteratedPoints:
    def __init__(self, **fields):
      self._fields = fields

    def __iter__(self):
      return iter([_NS(**self._fields)])

  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.sm["radarTracks"].points = _ReiteratedPoints(dRel=20.0, yRel=-1.8, vRel=-20.0, trackId=7)
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is True   # 静止点被视觉确认 → 仍然驱动计划
  expected = MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VRU_WEIGHT))
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(expected, rel=1e-6)


def test_daemon_suppresses_bias_during_lane_change():
  # modelV2.meta.laneChangeState != off: the model curvature is already
  # executing the lateral manoeuvre, so the avoidance bias must be suppressed
  # (planner gate, Task 6) and the frame published invalid with the raw model
  # curvature.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.sm._data["modelV2"].meta.laneChangeState = "preLaneChange"
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is False
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)


def test_daemon_projects_through_roi_inverse_mapping():
  # A box given in ROI pixels of a 1344x760 frame must land on the same
  # car-frame point as its full-frame equivalent.
  meta = RoiMeta(1344.0 / 640.0, 760.0 / 384.0, 0.0)
  d_cam = 20.0 + C.CAMERA_TO_FRONT
  u_full = CX - FX * (-1.8) / d_cam
  v_full = CY + FY * C.CAMERA_HEIGHT / d_cam
  u_roi, v_roi = u_full / meta.scale_u, v_full / meta.scale_v
  # Height in ROI px such that the FULL-FRAME height makes box-height ranging
  # return 20 m in the BUMPER frame (camera-frame 21.5 m; same convention as
  # _box_at — boxHeightPx is full-frame).
  h_roi = (FY * C.CLASS_HEIGHTS_M["person"] / (20.0 + C.CAMERA_TO_FRONT)) / meta.scale_v
  box = {"x1": u_roi - 5.0, "y1": v_roi - h_roi, "x2": u_roi + 5.0, "y2": v_roi, "cls": "person", "conf": 0.9}
  daemon, pm = _daemon(camera=_FakeCamera(frames=[(np.zeros((384, 640, 3), np.uint8), meta)] * 2),
                       detector=_FakeDetector(detections=[box]))
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  expected = MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VRU_WEIGHT))
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(expected, rel=1e-6)


# --- degradation ------------------------------------------------------------------

def test_daemon_degrades_to_radar_only_without_camera():
  class _DeadCamera:
    intrinsics = None

    def frame(self, horizon_row=None):
      return None

  daemon, pm = _daemon(camera=_DeadCamera(), radar_points=[(8.0, -1.8)])
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert "camera" in daemon.degraded          # logged once, radar-only fallback
  assert pm.sent[-1][1].valid is True         # the radar target still drives the plan
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature > MODEL_CURVATURE


def test_daemon_degrades_to_radar_only_when_yolo_fails():
  class _BrokenDetector:
    def __init__(self):
      self.calls = 0

    def infer(self, roi, now=None):
      self.calls += 1
      raise FileNotFoundError("pkl missing")

  detector = _BrokenDetector()
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4), detector=detector, radar_points=[(8.0, -1.8)])
  for i in range(4):
    daemon.update(i * C.DT_5HZ)
  assert "yolo" in daemon.degraded
  assert daemon.detector_dead is True
  assert detector.calls == 1                  # no retry spam after the failure
  assert pm.sent[-1][1].valid is True         # radar-only still works
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature > MODEL_CURVATURE


def test_daemon_creates_camera_lazily_via_factory():
  created = []

  class _Factory:
    def __call__(self):
      created.append(True)
      return _FakeCamera(frames=[ROI])

  daemon, _ = _daemon(camera_factory=_Factory(), detector=_FakeDetector())
  assert daemon.camera is None
  daemon.update(0.0)
  assert daemon.camera is not None
  assert created == [True]


def test_daemon_skips_vision_when_no_new_frame():
  # Camera connected but no fresh frame this tick: radar-only, no crash.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]), detector=_FakeDetector(detections=[_box_at(20.0, -1.8)]),
                       radar_points=[(8.0, -1.8)])
  daemon.update(0.0)                          # consumes the only frame
  daemon.update(C.DT_5HZ)                     # no frame -> radar-only this tick
  assert pm.sent[-1][1].valid is False        # enter hysteresis not satisfied yet
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)


def test_daemon_gates_valid_on_modelv2_validity():
  # Defensive: a frame whose modelV2 failed validation must never be published
  # as a valid plan, even with a target that would otherwise drive the bias.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]),
                       model_valid=False)
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert len(pm.sent) == 6                    # 3 streams x 2 frames, every-frame invariant holds
  assert pm.sent[-1][1].valid is False        # controlsd falls back to its own modelV2


def _edge_std_daemon(road_edge_stds):
  """右侧目标 + 左沿净空 1.0m（够）,仅路沿方差可变 —— 验证 daemon 把
  modelV2.roadEdgeStds 接进 planner 的 C7 置信门。"""
  edge = _NS(x=[10.0], y=[-1.0])   # 左沿净空 1.0 >= 0.6,本该放行
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[edge],
                 meta=_NS(laneChangeState="off"), roadEdgeStds=road_edge_stds)
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=8.0, yRel=-1.8, vRel=0.0)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams())
  return daemon, pm


def test_daemon_wires_edge_stds_into_planner_gate():
  # 左沿方差超标 -> 向左偏置被拦（valid=False,携带原始模型曲率）
  daemon, pm = _edge_std_daemon([0.9, 0.1])
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is False

  # 方差达标（左沿可信）-> 放行
  daemon, pm = _edge_std_daemon([0.1, 0.9])
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is True


def test_daemon_without_edge_stds_attribute_keeps_running():
  # 旧桩形态的 modelV2（无 roadEdgeStds 字段）:getattr 回退 None,行为不变
  edge = _NS(x=[10.0], y=[-1.0])
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[edge],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=8.0, yRel=-1.8, vRel=0.0)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  daemon = EagleDaemon(sm=_FakeSubMaster(model_v2, car_state, radar), pm=pm, params=_FakeParams())
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is True


def test_vision_is_gated_off_when_uncalibrated():
  """未标定时不得产生任何视觉目标,雷达路径不受影响。"""
  daemon, pm = _daemon()           # 沿用本文件现有 helper
  daemon.sm.valid["extrinsicsCalibration"] = True
  daemon.sm["extrinsicsCalibration"].calStatus = "uncalibrated"
  dets = daemon.perception.detect(daemon.sm['extrinsicsCalibration'], True, 0.0)
  assert dets == []
  assert daemon.degraded == {"calibration"}


def test_daemon_passes_frame_height_to_projection(monkeypatch):
  """截断框丢弃要在生产路径生效,perception core 必须把非 None 的 frame_height 传下去。"""
  import openpilot.selfdrive.eagled.perception as mod
  seen = []
  real = mod.project_detections

  def spy(dets, **kwargs):
    seen.append(kwargs.get("frame_height"))
    return real(dets, **kwargs)

  monkeypatch.setattr(mod, "project_detections", spy)
  daemon, _ = _daemon(camera=_FakeCamera(frames=[ROI]),
                      detector=_FakeDetector(detections=[_box_at(20.0, -1.0)]))
  daemon.perception.detect(daemon.sm['extrinsicsCalibration'], True, 0.0)
  assert seen == [760]              # 1344x760 帧高,非 None


# --- AvoidanceEnabled gates the vision chain (sensord starvation fix) --------------


class _MutableParams:
  """_FakeParams whose AvoidanceEnabled can be flipped mid-run."""

  def __init__(self):
    self.enabled = False

  def get_bool(self, key, block=False):
    return self.enabled

  def get(self, key, block=False, return_default=False):
    return None


def test_vision_chain_skipped_when_avoidance_disabled():
  """避让关:相机+YOLO 链路整段跳过(0 次推理),双流仍按帧发布、雷达路径不受影响。"""
  params = _MutableParams()
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[_NS(dRel=8.0, yRel=-1.8, vRel=0.0)],
              errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  sm = _FakeSubMaster(model_v2, car_state, radar,
                      valid={"modelV2": True, "carState": True, "radarTracks": True})
  camera = _FakeCamera(frames=[ROI])
  detector = _FakeDetector(detections=[_box_at(20.0, -1.0)])
  daemon = EagleDaemon(sm=sm, pm=pm, params=params, camera=camera, detector=detector)
  # >1s spacing: beat the params refresh throttle (PARAMS_REFRESH_PERIOD)
  daemon.update(0.0)
  daemon.update(PARAMS_REFRESH_PERIOD + 0.1)
  assert detector.calls == 0
  assert len(camera._frames) == 1                  # 相机未被消费
  st = [msg for service, msg in pm.sent if service == "eagleState"][-1].eagleState
  dbg = [msg for service, msg in pm.sent if service == "eagleDebug"][-1].eagleDebug
  assert st.nVision == 0 and dbg.nVision == 0      # 纯雷达帧
  assert st.nRadar == 1                            # 雷达行照常进画面
  plans = [msg for service, msg in pm.sent if service == "lateralManeuverPlan"]
  assert len(plans) == 2                           # 计划流不因视觉关闭而断


def test_vision_chain_resumes_after_avoidance_reenable():
  """避让关->开:惰性相机/检测器生命周期保留,推理无需重建即恢复。"""
  params = _MutableParams()
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[], errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  sm = _FakeSubMaster(model_v2, car_state, radar,
                      valid={"modelV2": True, "carState": True, "radarTracks": True})
  detector = _FakeDetector(detections=[_box_at(20.0, -1.0)])
  daemon = EagleDaemon(sm=sm, pm=pm, params=params, camera=_FakeCamera(frames=[ROI] * 2),
                       detector=detector)
  daemon.update(0.0)
  assert detector.calls == 0
  params.enabled = True
  daemon.update(PARAMS_REFRESH_PERIOD + 0.1)
  assert detector.calls == 1
  st = [msg for service, msg in pm.sent if service == "eagleState"][-1].eagleState
  assert st.nVision == 1


def test_vision_chain_kept_running_when_avoidance_enabled():
  """避让开(默认):行为与历史一致,每帧推理。"""
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0)]),
                       radar_points=[])
  daemon.update(0.0)
  assert daemon.detector.calls == 1


# --- vision hold: detections persist between vision ticks -------------------------
# a4abb7624 回归:vision_due 帧之间 detect() 返回 [],lanlink 的 eagleDebug 快照
# 被 ~4ms 后的纯雷达帧覆盖,车/人/自行车看起来消失。修复:两次推理之间沿用
# 上一次成功推理的检测(TTL 内),并按自车速度补偿纵向距离。

def _core_sm(v_ego=20.0):
  """PerceptionCore 直测用的最小 SubMaster(无 pm/planner 参与)。"""
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=v_ego, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[], errors=_NS(canError=False, radarUnavailableTemporary=False))
  return _FakeSubMaster(model_v2, car_state, radar)

def test_vision_targets_persist_between_vision_ticks():
  """非视觉帧沿用上次推理的检测:nVision 保持,不触发新推理。"""
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.update(0.0)   # due tick: 推理并缓存
  daemon.update(0.1)   # 非 due tick(< 0.2s 视觉节拍): 复用保持
  assert daemon.detector.calls == 1                    # 没有新推理
  st = [msg for service, msg in pm.sent if service == "eagleState"][-1].eagleState
  dbg = [msg for service, msg in pm.sent if service == "eagleDebug"][-1].eagleDebug
  assert st.nVision == 1 and dbg.nVision == 1
  rows = [t for t in dbg.targets if t.vision]
  assert len(rows) == 1 and rows[0].cls == "person"


def test_held_detections_compensate_ego_motion():
  """dRel 按自车速度收缩:dRel -= vEgo·age;yRel 横向不补偿。"""
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.update(0.0)                       # due tick: 框投影到 dRel 20
  daemon.sm._data["carState"].vEgo = 12.0  # 视觉帧之后自车减速
  daemon._next_vision_t = 5.0              # 强制下一帧非 due
  daemon.update(0.5)                       # 持有 0.5s
  dbg = [msg for service, msg in pm.sent if service == "eagleDebug"][-1].eagleDebug
  row = [t for t in dbg.targets if t.vision][0]
  assert row.dRel == pytest.approx(20.0 - 12.0 * 0.5)   # 14.0,不是 20.0
  assert row.yRel == pytest.approx(-1.8)


def test_held_detections_expire_after_ttl():
  """超过 TTL(1.0s,perception.VISION_HOLD_TTL_S)的保持检测清空,不会无限复活旧目标。"""
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.update(0.0)
  daemon.sm._data["carState"].vEgo = 0.0   # 隔离 TTL 过期与自车补偿
  daemon._next_vision_t = 5.0
  daemon.update(1.1)                       # > 1.0s TTL
  dbg = [msg for service, msg in pm.sent if service == "eagleDebug"][-1].eagleDebug
  assert dbg.nVision == 0


def test_held_detections_past_bumper_are_dropped():
  """补偿后越过保险杠(dRel <= 0)的目标已经过去了,丢弃而不是报负距离。"""
  core = PerceptionCore(camera=_FakeCamera(frames=[ROI]),
                        detector=_FakeDetector(detections=[_box_at(5.0, -1.8, cls="person")]))
  frame = core.process(_core_sm(), 0.0, 20.0, vision_enabled=True, vision_due=True)
  assert len(frame.detections) == 1
  frame = core.process(_core_sm(), C.DT_5HZ, 30.0, vision_enabled=True, vision_due=False)
  assert frame.detections == []            # 5 - 30*0.2 = -1.0 -> 丢


def test_hold_cleared_immediately_when_vision_disabled():
  """避让关闭立即清空保持:不让旧目标活过 TTL。"""
  core = PerceptionCore(camera=_FakeCamera(frames=[ROI]),
                        detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  core.process(_core_sm(), 0.0, 20.0, vision_enabled=True, vision_due=True)
  core.process(_core_sm(), C.DT_5HZ, 20.0, vision_enabled=False, vision_due=False)
  assert core.process(_core_sm(), 2 * C.DT_5HZ, 20.0, vision_enabled=True, vision_due=False).detections == []
  assert core.last_vision_duration_s == 0.0


def test_vision_duration_resets_when_camera_frame_missing():
  """相机当拍无帧:清零 last_vision_duration_s,不让旧耗时污染节流原因。"""
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8)]))
  daemon.update(0.0)                              # due,推理一次
  daemon.perception.last_vision_duration_s = 0.5  # 人为注入旧耗时
  daemon.update(C.DT_5HZ)                         # due(0.2),相机无帧 -> 降级
  assert daemon.perception.last_vision_duration_s == 0.0
