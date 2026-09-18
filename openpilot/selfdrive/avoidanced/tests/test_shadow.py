"""Tests for the offline P0 shadow evaluation harness (no publishing)."""

import json
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.avoidanced.avoidance_planner import AvoidancePlanner
from openpilot.selfdrive.avoidanced.shadow import (ASSOC_MAX_DY, CALIB_MAX_RESIDUAL_M, MAX_LATERAL_JERK, RadarTarget,
                                                   ShadowEvaluator, ShadowFrame, VisionObject, associate, evaluate_records,
                                                   iter_frames, summarize, write_report)


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
