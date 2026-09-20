"""Daemon-level fusion tests: stub camera + detector wired into AvoidanceDaemon."""

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.avoidanced import AvoidanceDaemon
from openpilot.selfdrive.avoidanced.projection import RoiMeta

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
                  "extrinsicsCalibration": _NS(calStatus=cal_status, rpyCalib=list(rpy))}
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
  """ROI box (scale 1:1) whose bottom-centre projects to (d_rel, y_rel)."""
  d_cam = d_rel + C.CAMERA_TO_FRONT
  v = CY + FY * C.CAMERA_HEIGHT / d_cam
  u = CX - FX * y_rel / d_cam
  return {"x1": u - 10.0, "y1": v - 20.0, "x2": u + 10.0, "y2": v, "cls": cls, "conf": conf}


def _daemon(*, camera=None, detector=None, camera_factory=None, radar_points=(), enabled=True,
            model_valid=True):
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[])
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
  daemon = AvoidanceDaemon(sm=sm, pm=pm, params=_FakeParams(enabled=enabled), **kwargs)
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
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]))
  daemon.update(0.0)                    # enter hysteresis
  daemon.update(C.ENTER_HOLD_S + 0.01)  # active
  assert pm.sent[-1][1].valid is True
  expected = MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VRU_WEIGHT))
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(expected, rel=1e-6)


def test_daemon_vru_weight_exceeds_vehicle_weight():
  frames = [ROI] * 2
  person, _ = _daemon(camera=_FakeCamera(frames),
                      detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]))
  car, _ = _daemon(camera=_FakeCamera(frames),
                   detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="car")]))
  for d in (person, car):
    d.update(0.0)
    d.update(C.ENTER_HOLD_S + 0.01)
  person_curv = person.pm.sent[-1][1].lateralManeuverPlan.desiredCurvature
  car_curv = car.pm.sent[-1][1].lateralManeuverPlan.desiredCurvature
  assert person_curv > car_curv > MODEL_CURVATURE
  assert car_curv == pytest.approx(MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VEHICLE_WEIGHT)), rel=1e-6)


def test_daemon_associated_detection_not_double_counted():
  # Radar point and YOLO box at the same spot: the radar point absorbs the
  # detection (one target, vehicle weight) instead of stacking two targets.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       radar_points=[(20.0, -1.0)])
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert pm.sent[-1][1].valid is True
  expected = MODEL_CURVATURE + _first_frame_bias(_expected_offset(20.0, C.VEHICLE_WEIGHT))
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(expected, rel=1e-6)


def test_daemon_projects_through_roi_inverse_mapping():
  # A box given in ROI pixels of a 1344x760 frame must land on the same
  # car-frame point as its full-frame equivalent.
  meta = RoiMeta(1344.0 / 640.0, 760.0 / 384.0, 0.0)
  d_cam = 20.0 + C.CAMERA_TO_FRONT
  u_full = CX - FX * (-1.0) / d_cam
  v_full = CY + FY * C.CAMERA_HEIGHT / d_cam
  u_roi, v_roi = u_full / meta.scale_u, v_full / meta.scale_v
  box = {"x1": u_roi - 5.0, "y1": v_roi - 10.0, "x2": u_roi + 5.0, "y2": v_roi, "cls": "person", "conf": 0.9}
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

  daemon, pm = _daemon(camera=_DeadCamera(), radar_points=[(8.0, -1.0)])
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
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4), detector=detector, radar_points=[(8.0, -1.0)])
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
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]), detector=_FakeDetector(detections=[_box_at(20.0, -1.0)]),
                       radar_points=[(8.0, -1.0)])
  daemon.update(0.0)                          # consumes the only frame
  daemon.update(C.DT_5HZ)                     # no frame -> radar-only this tick
  assert pm.sent[-1][1].valid is False        # enter hysteresis not satisfied yet
  assert pm.sent[-1][1].lateralManeuverPlan.desiredCurvature == pytest.approx(MODEL_CURVATURE)


def test_daemon_gates_valid_on_modelv2_validity():
  # Defensive: a frame whose modelV2 failed validation must never be published
  # as a valid plan, even with a target that would otherwise drive the bias.
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       model_valid=False)
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  assert len(pm.sent) == 4                    # every-frame publish invariant holds (debug+plan)
  assert pm.sent[-1][1].valid is False        # controlsd falls back to its own modelV2


def test_vision_is_gated_off_when_uncalibrated():
  """未标定时不得产生任何视觉目标,雷达路径不受影响。"""
  daemon, pm = _daemon()           # 沿用本文件现有 helper
  daemon.sm.valid["extrinsicsCalibration"] = True
  daemon.sm["extrinsicsCalibration"].calStatus = "uncalibrated"
  dets = daemon._detect(0.0)
  assert dets == []
  assert daemon.degraded == {"calibration"}


def test_daemon_passes_frame_height_to_projection(monkeypatch):
  """截断框丢弃要在生产路径生效,daemon 必须把非 None 的 frame_height 传下去。"""
  import openpilot.selfdrive.avoidanced.avoidanced as mod
  seen = []
  real = mod.project_detections

  def spy(dets, **kwargs):
    seen.append(kwargs.get("frame_height"))
    return real(dets, **kwargs)

  monkeypatch.setattr(mod, "project_detections", spy)
  daemon, _ = _daemon(camera=_FakeCamera(frames=[ROI]),
                      detector=_FakeDetector(detections=[_box_at(20.0, -1.0)]))
  daemon._detect(0.0)
  assert seen == [760]              # 1344x760 帧高,非 None
