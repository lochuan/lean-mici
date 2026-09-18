"""Tests for the online calibration collector (pure fit + extraction + CLI semantics)."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.calibrate import (CalibPair, MIN_FIT_PAIRS, RESIDUAL_PASS_M, extract_pairs,
                                                      fit_calibrated_offsets, format_constants_block, main)


def _synth_pairs(n=200, d_front=0.0, d_pitch=0.0, d_yaw=0.0, lateral_bias=0.0,
                 noise=0.0, seed=0):
  """Radar positions are the truth; vision = truth + injected constant errors."""
  rng = np.random.default_rng(seed)
  pairs = []
  for d, y in zip(rng.uniform(5.0, 45.0, n), rng.uniform(-2.0, 2.0, n), strict=False):
    e_d = d_front + d_pitch * d ** 2 / C.CAMERA_HEIGHT + rng.normal(0.0, noise)
    e_y = d_yaw * d + lateral_bias + rng.normal(0.0, noise)
    pairs.append(CalibPair(d_radar=float(d), y_radar=float(y),
                           d_vision=float(d + e_d), y_vision=float(y + e_y), v_ego=20.0))
  return pairs


# --- fit ----------------------------------------------------------------------

def test_fit_recovers_injected_constants_exact():
  pairs = _synth_pairs(d_front=0.3, d_pitch=math.radians(0.5), d_yaw=math.radians(0.3))
  result = fit_calibrated_offsets(pairs)
  assert result["d_front_m"] == pytest.approx(0.3, abs=1e-6)
  assert result["d_pitch_rad"] == pytest.approx(math.radians(0.5), abs=1e-9)
  assert result["d_yaw_rad"] == pytest.approx(math.radians(0.3), abs=1e-9)
  assert result["residual_p95_after_m"] == pytest.approx(0.0, abs=1e-6)
  assert result["pass"] is True
  assert result["warnings"] == []


def test_fit_recovers_injected_constants_with_noise():
  pairs = _synth_pairs(n=400, d_front=0.3, d_pitch=math.radians(0.5), d_yaw=math.radians(0.3),
                       noise=0.03, seed=42)
  result = fit_calibrated_offsets(pairs)
  assert result["d_front_m"] == pytest.approx(0.3, abs=0.05)
  assert result["d_pitch_rad"] == pytest.approx(math.radians(0.5), abs=0.002)
  assert result["d_yaw_rad"] == pytest.approx(math.radians(0.3), abs=0.002)


def test_fit_zero_residual_gives_zero_suggestions_and_passes():
  result = fit_calibrated_offsets(_synth_pairs())
  assert result["d_front_m"] == pytest.approx(0.0, abs=1e-9)
  assert result["d_pitch_rad"] == pytest.approx(0.0, abs=1e-12)
  assert result["d_yaw_rad"] == pytest.approx(0.0, abs=1e-12)
  assert result["residual_p95_after_m"] == pytest.approx(0.0, abs=1e-9)
  assert result["pass"] is True


def test_fit_too_few_pairs_raises():
  with pytest.raises(ValueError, match="pairs"):
    fit_calibrated_offsets(_synth_pairs(n=MIN_FIT_PAIRS - 1))


def test_fit_reports_lateral_bias_as_warning_without_absorbing_it():
  # A constant lateral residual must surface as a warning; the yaw fit stays
  # on the distance-proportional term and must not eat the constant.
  pairs = _synth_pairs(d_yaw=math.radians(0.3), lateral_bias=0.15)
  result = fit_calibrated_offsets(pairs)
  assert result["lateral_bias_m"] == pytest.approx(0.15, abs=0.01)
  assert any("lateral mount" in w for w in result["warnings"])
  assert result["d_yaw_rad"] == pytest.approx(math.radians(0.3), abs=1e-9)


def test_fit_reports_p95_improvement():
  pairs = _synth_pairs(d_front=0.5, d_pitch=math.radians(1.0), d_yaw=math.radians(0.5))
  result = fit_calibrated_offsets(pairs)
  assert result["residual_p95_before_m"] > 0.5
  assert result["residual_p95_after_m"] < 0.05


def test_format_constants_block_applies_increments():
  pairs = _synth_pairs(d_front=0.3, d_pitch=math.radians(0.5), d_yaw=math.radians(0.3))
  block = format_constants_block(fit_calibrated_offsets(pairs))
  assert f"CAMERA_TO_FRONT = {C.CAMERA_TO_FRONT + 0.3:.4f}" in block
  assert f"CAMERA_PITCH = {C.CAMERA_PITCH + math.radians(0.5):.6f}" in block
  assert f"CAMERA_YAW = {C.CAMERA_YAW + math.radians(0.3):.6f}" in block


# --- pair extraction ------------------------------------------------------------

def _target(d_rel, y_rel, pair_id=0, vision=False):
  return SimpleNamespace(dRel=d_rel, yRel=y_rel, pairId=pair_id, vision=vision)


def test_extract_pairs_matches_shared_pair_ids():
  targets = [
    _target(20.0, -1.0, pair_id=1, vision=False),
    _target(20.4, -1.1, pair_id=1, vision=True),
    _target(30.0, 0.5, pair_id=2, vision=False),
    _target(30.3, 0.4, pair_id=2, vision=True),
  ]
  pairs = extract_pairs(targets, v_ego=15.0)
  assert len(pairs) == 2
  assert {(p.d_radar, p.d_vision) for p in pairs} == {(20.0, 20.4), (30.0, 30.3)}
  assert all(p.v_ego == 15.0 for p in pairs)


def test_extract_pairs_ignores_unpaired_and_degenerate_groups():
  targets = [
    _target(10.0, 0.0, pair_id=0, vision=False),          # unpaired radar
    _target(10.0, 0.0, pair_id=0, vision=True),           # unpaired vision
    _target(20.0, -1.0, pair_id=3, vision=False),         # radar-only group
    _target(25.0, 1.0, pair_id=4, vision=True),           # vision-only group
  ]
  assert extract_pairs(targets, v_ego=10.0) == []


# --- CLI semantics ----------------------------------------------------------------

class _FakeSM:
  """Yields one pre-built avoidanceDebug frame per update() call."""

  def __init__(self, frames):
    self._frames = list(frames)
    self.updated = {}

  def update(self, timeout):
    if self._frames:
      self._current = self._frames.pop(0)
      self.updated = {"avoidanceDebug": True}
    else:
      self.updated = {}

  def __getitem__(self, service):
    return self._current


class _FakeClock:
  def __init__(self, step=0.2):
    self.t = 0.0
    self.step = step

  def __call__(self):
    self.t += self.step
    return self.t


def _debug_frame(pairs_spec):
  targets = []
  for i, (d, y) in enumerate(pairs_spec, start=1):
    targets.append(_target(d, y, pair_id=i, vision=False))
    targets.append(_target(d, y, pair_id=i, vision=True))
  return SimpleNamespace(targets=targets, vEgo=20.0)


def test_main_insufficient_pairs_exits_1():
  sm = _FakeSM([_debug_frame([(20.0, -1.0)])])  # 1 pair < min 30
  code = main(["--duration", "10", "--min-pairs", "30"], sm_factory=lambda: sm,
              clock=_FakeClock(), sleep=lambda s: None)
  assert code == 1


def test_main_collects_fits_and_passes_on_clean_data():
  frames = [_debug_frame([(10.0 + i * 0.5, -1.0)]) for i in range(40)]
  sm = _FakeSM(frames)
  code = main(["--duration", "10", "--min-pairs", "30", "--max-pairs", "500"],
              sm_factory=lambda: sm, clock=_FakeClock(), sleep=lambda s: None)
  assert code == 0  # zero residual -> p95 0.0 -> pass


def test_main_fails_on_out_of_tolerance_residual():
  # Constant 0.5 m forward offset: post-correction p95 is fine (it is absorbed),
  # so instead inject unmodelable noise to push the corrected p95 over 0.3 m.
  rng = np.random.default_rng(1)
  frames = []
  for i in range(40):
    d, y = 10.0 + i, -1.0
    targets = [_target(d, y, pair_id=1, vision=False),
               _target(d + rng.normal(0.0, 0.5), y + rng.normal(0.0, 0.5), pair_id=1, vision=True)]
    frames.append(SimpleNamespace(targets=targets, vEgo=20.0))
  sm = _FakeSM(frames)
  code = main(["--duration", "10", "--min-pairs", "30"], sm_factory=lambda: sm,
              clock=_FakeClock(), sleep=lambda s: None)
  assert code == 1


def test_residual_threshold_matches_spec():
  assert RESIDUAL_PASS_M == pytest.approx(0.30)
