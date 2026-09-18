"""Tests for the offline P0 shadow evaluation harness (no publishing)."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.avoidance_planner import AvoidancePlanner
from openpilot.selfdrive.avoidanced.projection import RoiMeta
from openpilot.selfdrive.avoidanced.shadow import (ASSOC_MAX_DY, CALIB_MAX_RESIDUAL_M, MAX_LATERAL_JERK, RadarTarget,
                                                   ShadowEvaluator, ShadowFrame, VisionObject, associate, evaluate_log,
                                                   evaluate_records, iter_frames, summarize, write_report)


class _Msg:
  def __init__(self, which, t, **attrs):
    self._which = which
    self.logMonoTime = int(t * 1e9)
    for k, v in attrs.items():
      setattr(self, k, v)

  def which(self):
    return self._which


def _model(curvature=0.01, leads=()):
  return SimpleNamespace(action=SimpleNamespace(desiredCurvature=curvature), roadEdges=[],
                         leadsV3=[SimpleNamespace(prob=p, x=[x, x], y=[y, y]) for p, x, y in leads])


def _car(v_ego=20.0, left=False, right=False, steering=False):
  return SimpleNamespace(vEgo=v_ego, leftBlindspot=left, rightBlindspot=right, steeringPressed=steering)


def _radar(points):
  return SimpleNamespace(points=[SimpleNamespace(dRel=d, yRel=y, vRel=0.0) for d, y in points])


def _frame(t, radar=(), vision=(), curvature=0.01, v_ego=20.0, **kwargs):
  return ShadowFrame(t=t, model_curvature=curvature, v_ego=v_ego,
                     radar_points=[RadarTarget(*p) for p in radar],
                     vision_objects=[VisionObject(*v) for v in vision], **kwargs)


# --- association -------------------------------------------------------------

def test_associate_matches_nearest_within_gates():
  radar = [RadarTarget(10.0, -1.0)]
  vision = [VisionObject(10.4, -1.2), VisionObject(30.0, 0.0)]
  pairs = associate(radar, vision)
  assert len(pairs) == 1
  assert pairs[0][1].x == pytest.approx(10.4)


def test_associate_rejects_out_of_gate():
  radar = [RadarTarget(10.0, -1.0)]
  vision = [VisionObject(10.0, -1.0 + ASSOC_MAX_DY + 0.1)]
  assert associate(radar, vision) == []


def test_associate_handles_empty_inputs():
  assert associate([], [VisionObject(1.0, 0.0)]) == []
  assert associate([RadarTarget(1.0, 0.0)], []) == []


# --- evaluator ---------------------------------------------------------------

def test_evaluator_records_association_and_calibration():
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0))
  # 10 radar targets, 9 corroborated by vision, one far ghost with no vision match.
  radar = [(10.0 + i, -0.5) for i in range(9)] + [(35.0, -0.5)]
  vision = [(10.0 + i, -0.5) for i in range(9)]
  evaluator.step(_frame(0.0, radar=radar, vision=vision))
  rec = evaluator.records[-1]
  assert rec.n_radar == 10
  assert rec.n_associated == 9
  assert rec.lat_residual == pytest.approx(0.0, abs=1e-9)


def test_evaluator_never_publishes_only_records():
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0))
  evaluator.run([_frame(0.0, radar=[(8.0, -1.0)]), _frame(0.2, radar=[(8.0, -1.0)])])
  assert len(evaluator.records) == 2
  assert not hasattr(evaluator, "pm")


def test_evaluator_jerk_from_curvature_rate():
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0))
  # First frame establishes the baseline curvature; second jumps by 0.001 over 0.2s.
  evaluator.step(_frame(0.0, curvature=0.010))
  rec = evaluator.step(_frame(0.2, curvature=0.011, v_ego=20.0))
  expected = 20.0 ** 2 * (0.011 - 0.010) / 0.2
  assert rec.jerk == pytest.approx(expected)


# --- fused vision path (detector injection) -----------------------------------

# Synthetic wide-camera intrinsics (full frame 1344x760, focal 425.25), same as
# the daemon fusion tests.
FX = FY = 425.25
CX, CY = 672.0, 380.0


def _box_at(d_rel, y_rel, cls="person", conf=0.9):
  """ROI box (1:1 scale) whose bottom-centre projects to (d_rel, y_rel)."""
  d_cam = d_rel + C.CAMERA_TO_FRONT
  v = CY + FY * C.CAMERA_HEIGHT / d_cam
  u = CX - FX * y_rel / d_cam
  return {"x1": u - 10.0, "y1": v - 20.0, "x2": u + 10.0, "y2": v, "cls": cls, "conf": conf}


class _StubDetector:
  def __init__(self, detections=()):
    self._detections = list(detections)
    self.calls = 0

  def infer(self, roi, now=None):
    self.calls += 1
    return self._detections


def _fused_frame(t, radar=(), curvature=0.01, v_ego=20.0, **kwargs):
  return ShadowFrame(t=t, model_curvature=curvature, v_ego=v_ego,
                     radar_points=[RadarTarget(*p) for p in radar],
                     roi_frame=np.zeros((384, 640, 3), np.uint8),
                     roi_meta=RoiMeta(1.0, 1.0, 0.0),
                     intrinsics=(FX, FY, CX, CY), **kwargs)


def _first_frame_curvature(weight):
  alpha = C.DT_5HZ / (C.LOWPASS_TAU_S + C.DT_5HZ)
  magnitude = min(C.MAX_OFFSET_FREE, C.K_GAIN * weight * (1.0 - 20.0 / C.D_MAX))
  return 0.01 + 2.0 * magnitude / C.L_LOOKAHEAD ** 2 * alpha


def test_detector_path_projects_and_associates():
  detector = _StubDetector([_box_at(20.0, -1.0)])
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0), detector=detector)
  evaluator.step(_fused_frame(0.0, radar=[(20.0, -1.0)]))
  rec = evaluator.records[-1]
  assert rec.n_vision == 1                    # n_vision comes from the projected detections
  assert rec.n_associated == 1                # radar point corroborated by the YOLO box
  assert rec.lat_residual == pytest.approx(0.0, abs=1e-9)
  assert detector.calls == 1


def test_detector_path_absorbed_detection_keeps_vehicle_weight():
  # Daemon-parity association: the radar point absorbs the co-located person
  # box, so the planner sees ONE target carrying the vehicle weight.
  detector = _StubDetector([_box_at(20.0, -1.0, cls="person")])
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0), detector=detector)
  evaluator.step(_fused_frame(0.0, radar=[(20.0, -1.0)]))
  evaluator.step(_fused_frame(C.ENTER_HOLD_S + 0.01, radar=[(20.0, -1.0)]))
  rec = evaluator.records[-1]
  assert rec.valid is True
  assert rec.curvature == pytest.approx(_first_frame_curvature(C.VEHICLE_WEIGHT), rel=1e-6)


def test_detector_path_unmatched_detection_is_independent_vru_target():
  # Radar sees nothing; the projected person box drives the bias on its own.
  detector = _StubDetector([_box_at(20.0, -1.0, cls="person")])
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0), detector=detector)
  evaluator.step(_fused_frame(0.0))
  evaluator.step(_fused_frame(C.ENTER_HOLD_S + 0.01))
  rec = evaluator.records[-1]
  assert rec.n_vision == 1
  assert rec.n_associated == 0
  assert rec.valid is True
  assert rec.curvature == pytest.approx(_first_frame_curvature(C.VRU_WEIGHT), rel=1e-6)


def test_proxy_path_kept_without_detector():
  # No detector injected: even a frame carrying camera data stays on the
  # leadsV3 proxy (route replay has no YOLO boxes anyway).
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0))
  frame = _frame(0.0, radar=[(20.0, -1.0)], vision=[(20.0, -1.0)],
                 roi_frame=np.zeros((384, 640, 3), np.uint8), roi_meta=RoiMeta(1.0, 1.0, 0.0),
                 intrinsics=(FX, FY, CX, CY))
  rec = evaluator.step(frame)
  assert rec.n_vision == 1
  assert rec.n_associated == 1


def test_detector_path_requires_camera_data():
  detector = _StubDetector()
  evaluator = ShadowEvaluator(planner=AvoidancePlanner(clock=lambda: 0.0), detector=detector)
  with pytest.raises(ValueError):
    evaluator.step(_frame(0.0, radar=[(20.0, -1.0)]))
  assert detector.calls == 0


# --- summary -----------------------------------------------------------------

def _record(**kwargs):
  from openpilot.selfdrive.avoidanced.shadow import ShadowRecord
  defaults = {"t": 0.0, "model_curvature": 0.0, "curvature": 0.0, "valid": False, "y_des": 0.0,
              "n_radar": 0, "n_vision": 0, "n_associated": 0, "lat_residual": None, "latency_ms": 1.0, "jerk": None}
  defaults.update(kwargs)
  return ShadowRecord(**defaults)


def test_summarize_computes_rates_and_passes_clean_data():
  records = [
    _record(t=0.0, n_radar=10, n_associated=9, lat_residual=0.1, jerk=1.0),
    _record(t=0.2, n_radar=10, n_associated=8, lat_residual=0.2, jerk=2.0, valid=True, y_des=0.1),
  ]
  s = summarize(records)
  assert s["association_rate"] == pytest.approx(17 / 20)
  assert s["calibration_max_residual_m"] == pytest.approx(0.2)
  assert s["max_lateral_jerk"] == pytest.approx(2.0)
  assert s["pass"] is True
  assert s["insufficient_data"] is False


def test_summarize_fails_low_association():
  records = [_record(n_radar=10, n_associated=5)]
  s = summarize(records)
  assert s["association_rate"] == pytest.approx(0.5)
  assert s["pass"] is False


def test_summarize_fails_calibration_drift():
  records = [_record(n_radar=1, n_associated=1, lat_residual=CALIB_MAX_RESIDUAL_M + 0.01)]
  s = summarize(records)
  assert s["pass"] is False


def test_summarize_fails_excess_jerk():
  records = [_record(n_radar=1, n_associated=1, jerk=MAX_LATERAL_JERK + 0.1)]
  s = summarize(records)
  assert s["pass"] is False


def test_summarize_flags_insufficient_data_without_radar():
  s = summarize([_record(), _record()])
  assert s["insufficient_data"] is True
  assert s["association_rate"] is None


def test_summarize_counts_false_triggers_and_activations():
  records = [
    _record(t=0.0, valid=True, y_des=0.1, n_radar=1, n_associated=1),   # corroborated
    _record(t=0.2, valid=True, y_des=0.1, n_radar=1, n_associated=0),   # false trigger (ghost)
    _record(t=0.4, valid=False),
    _record(t=0.6, valid=True, y_des=0.1, n_radar=1, n_associated=1),   # new activation segment
  ]
  s = summarize(records)
  assert s["false_trigger_frames"] == 1
  assert s["activation_segments"] == 2
  assert s["valid_frames"] == 3


# --- frame extraction --------------------------------------------------------

def test_iter_frames_samples_at_5hz_and_carries_latest_state():
  msgs = [
    _Msg("modelV2", 0.0, modelV2=_model(leads=[(0.9, 10.0, -1.0)])),
    _Msg("carState", 0.0, carState=_car()),
    _Msg("radarTracks", 0.0, radarTracks=_radar([(10.0, -1.0)])),
    _Msg("radarTracks", 0.1, radarTracks=_radar([(10.0, -1.0)])),
    _Msg("radarTracks", 0.25, radarTracks=_radar([(10.0, -1.0)])),
  ]
  frames = list(iter_frames(msgs, sample_period=0.2))
  assert [f.t for f in frames] == pytest.approx([0.0, 0.25])
  assert frames[0].radar_points[0].dRel == pytest.approx(10.0)
  assert frames[0].vision_objects[0].y == pytest.approx(-1.0)
  assert frames[0].v_ego == pytest.approx(20.0)


def test_iter_frames_skips_until_state_present():
  msgs = [
    _Msg("radarTracks", 0.0, radarTracks=_radar([])),
    _Msg("modelV2", 0.0, modelV2=_model()),
    _Msg("carState", 0.0, carState=_car()),
    _Msg("radarTracks", 0.3, radarTracks=_radar([])),
  ]
  frames = list(iter_frames(msgs, sample_period=0.2))
  assert len(frames) == 1


# --- report ------------------------------------------------------------------

def test_write_report_emits_csv_and_json(tmp_path):
  records = [_record(t=0.0, n_radar=1, n_associated=1, lat_residual=0.05, valid=True, y_des=0.1)]
  summary = summarize(records)
  csv_path, json_path = write_report(records, summary, tmp_path)
  assert csv_path.exists() and json_path.exists()
  data = json.loads(json_path.read_text())
  assert data["pass"] is True
  assert "valid" in csv_path.read_text().splitlines()[0]


def test_evaluate_records_runs_end_to_end():
  frames = [_frame(0.0, radar=[(8.0, -1.0)], vision=[(8.0, -1.0)]),
            _frame(0.2, radar=[(8.0, -1.0)], vision=[(8.0, -1.0)])]
  records, summary = evaluate_records(frames, planner=AvoidancePlanner(clock=lambda: 0.0))
  assert len(records) == 2
  assert summary["frames"] == 2


# --- synthetic-log E2E (brief step 3) ------------------------------------------

def test_synthetic_log_end_to_end(tmp_path):
  """Synthetic cereal log through evaluate_log + main(): proxy vision, pass=True, exit 0."""
  from openpilot.selfdrive.avoidanced.shadow import main

  import openpilot.cereal.messaging as messaging

  msgs = []
  for i in range(60):  # 3 s at 20 Hz
    t = i * 0.05
    car = messaging.new_message('carState')
    car.logMonoTime = int(t * 1e9)
    car.carState.vEgo = 20.0
    model = messaging.new_message('modelV2')
    model.logMonoTime = int(t * 1e9)
    model.modelV2.action.desiredCurvature = 0.01
    model.modelV2.init('leadsV3', 1)
    lead = model.modelV2.leadsV3[0]
    lead.prob = 0.9
    lead.x = [10.0, 10.0]
    lead.y = [-1.0, -1.0]
    radar = messaging.new_message('radarTracks')
    radar.logMonoTime = int(t * 1e9)
    radar.radarTracks.init('points', 1)
    point = radar.radarTracks.points[0]
    point.dRel, point.yRel, point.vRel = 10.0, -1.0, 0.0
    msgs.extend([car, model, radar])

  log_path = tmp_path / "synthetic"  # no extension: read as an uncompressed raw log
  # save_log expects readers (as_builder); new_message returns builders whose
  # to_bytes() is the same serialization save_log would write.
  log_path.write_bytes(b"".join(m.to_bytes() for m in msgs))

  summary = evaluate_log(str(log_path), out_dir=str(tmp_path / "out"))
  assert summary["frames"] > 0
  assert summary["association_rate"] == pytest.approx(1.0)
  assert summary["calibration_max_residual_m"] == pytest.approx(0.0, abs=1e-6)
  assert summary["pass"] is True
  assert main([str(log_path), "--out", str(tmp_path / "out2")]) == 0
