"""Tests for the online calibration collector (pure fit + extraction + CLI semantics).

Pair injection round-trips the REAL ``project_box_to_vehicle``: pixels are
generated with the true constants (current + deltas) via the inverse mapping,
then projected back with the current constants. The residuals therefore carry
the true sensor geometry (including the projection's yaw/pitch sign
conventions) — the tests never share a hand-derived error model with the fit.
"""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.calibrate import (CalibPair, MIN_FIT_PAIRS, RESIDUAL_PASS_M, extract_pairs,
                                                      fit_calibrated_offsets, format_constants_block, main)
from openpilot.selfdrive.avoidanced.projection import project_box_to_vehicle

# Synthetic wide-camera intrinsics (full frame 1344x760, focal 425.25), same as
# the shadow/daemon fusion tests.
FX = FY = 425.25
CX, CY = 672.0, 380.0


def _pixel_at(d_rel, y_rel, ctf, pitch, yaw, height=C.CAMERA_HEIGHT):
  """Inverse of project_box_to_vehicle: bumper-frame ground point -> full-frame pixel."""
  x = d_rel + ctf
  cos_y, sin_y = math.cos(yaw), math.sin(yaw)
  xg = x * cos_y + y_rel * sin_y
  yg = -x * sin_y + y_rel * cos_y
  cos_p, sin_p = math.cos(pitch), math.sin(pitch)
  y_n = (height * cos_p - xg * sin_p) / (xg * cos_p + height * sin_p)
  x_n = -(yg / xg) * (cos_p - y_n * sin_p)
  return CX + FX * x_n, CY + FY * y_n


def _synth_pairs(d_front=0.0, d_pitch=0.0, d_yaw=0.0, lateral_bias=0.0,
                 noise=0.0, n=200, seed=0, current=None, true=None):
  """Pairs whose vision side comes from the real projection round-trip.

  ``current`` is the (ctf, pitch, yaw) triple the projection runs with; the
  pixels are generated with the TRUE mount — by default ``current + deltas``,
  or the explicit ``true`` triple (pass it to keep the mount fixed across
  fit/apply iterations).
  """
  current = current if current is not None else (C.CAMERA_TO_FRONT, C.CAMERA_PITCH, C.CAMERA_YAW)
  true = true if true is not None else (current[0] + d_front, current[1] + d_pitch, current[2] + d_yaw)
  rng = np.random.default_rng(seed)
  pairs = []
  for d, y in zip(rng.uniform(5.0, 45.0, n), rng.uniform(-2.0, 2.0, n), strict=True):
    u, v = _pixel_at(float(d), float(y), *true)
    vis = project_box_to_vehicle(u=u, v=v, fx=FX, fy=FY, cx=CX, cy=CY, height=C.CAMERA_HEIGHT,
                                 pitch=current[1], yaw=current[2], camera_to_front=current[0])
    pairs.append(CalibPair(d_radar=float(d), y_radar=float(y),
                           d_vision=vis["dRel"] + rng.normal(0.0, noise),
                           y_vision=vis["yRel"] + lateral_bias + rng.normal(0.0, noise),
                           v_ego=20.0))
  return pairs


# --- fit ----------------------------------------------------------------------

# Real-projection residuals are only approximately linear in the constants
# (the pitch term gains a constant Δpitch·h piece and grows faster than d²/h at
# range), so recovery tolerances are statistical, not exact; the round-trip
# convergence test below pins the end-to-end behaviour.

def test_fit_recovers_injected_constants():
  pairs = _synth_pairs(d_front=0.3, d_pitch=math.radians(0.1), d_yaw=math.radians(0.3))
  result = fit_calibrated_offsets(pairs)
  assert result["d_front_m"] == pytest.approx(0.3, abs=0.06)
  assert result["d_pitch_rad"] == pytest.approx(math.radians(0.1), rel=0.25)
  assert result["d_yaw_rad"] == pytest.approx(math.radians(0.3), rel=0.10)
  assert result["residual_p95_after_m"] < RESIDUAL_PASS_M
  assert result["pass"] is True
  assert result["warnings"] == []


def test_fit_recovers_injected_constants_with_noise():
  pairs = _synth_pairs(n=400, d_front=0.3, d_pitch=math.radians(0.1), d_yaw=math.radians(0.3),
                       noise=0.03, seed=42)
  result = fit_calibrated_offsets(pairs)
  assert result["d_front_m"] == pytest.approx(0.3, abs=0.06)
  assert result["d_pitch_rad"] == pytest.approx(math.radians(0.1), rel=0.25)
  assert result["d_yaw_rad"] == pytest.approx(math.radians(0.3), rel=0.10)


def test_fit_yaw_suggestion_sign_matches_projection():
  # Reviewer's numeric case: a camera truly yawed LEFT by +0.05 rad relative to
  # the compiled constant produces e_y slope = -0.04998 (= -sin 0.05, verified
  # against project_box_to_vehicle); the suggestion must be the NEGATED slope,
  # and applying it must shrink the lateral residual, not double it.
  pairs = _synth_pairs(d_yaw=0.05, n=300, seed=7)
  result = fit_calibrated_offsets(pairs)
  assert result["d_yaw_rad"] > 0.0
  assert result["d_yaw_rad"] == pytest.approx(math.sin(0.05), abs=0.002)
  e_y_before = np.mean(np.abs([p.y_vision - p.y_radar for p in pairs]))
  e_y_after = np.mean(np.abs([p.y_vision + result["d_yaw_rad"] * p.d_radar - p.y_radar for p in pairs]))
  assert e_y_after < e_y_before / 10.0


def test_roundtrip_convergence_two_iterations():
  # Applying the suggestions and re-fitting must monotonically shrink the raw
  # residual across two iterations (linearized fit of a mildly nonlinear
  # geometry: round 1 removes the bulk, round 2 the cross-terms). The true
  # mount stays FIXED while the compiled constants converge onto it.
  base = (C.CAMERA_TO_FRONT, C.CAMERA_PITCH, C.CAMERA_YAW)
  true = (base[0] + 0.3, base[1] + math.radians(0.1), base[2] + math.radians(0.3))
  current = list(base)
  p95s = []
  for _ in range(3):
    pairs = _synth_pairs(n=300, seed=7, current=tuple(current), true=true)
    result = fit_calibrated_offsets(pairs)
    p95s.append(result["residual_p95_before_m"])
    current = [current[0] + result["d_front_m"], current[1] + result["d_pitch_rad"], current[2] + result["d_yaw_rad"]]
  assert p95s[0] > p95s[1] > p95s[2]
  assert p95s[2] < 0.1


def test_fit_zero_residual_gives_zero_suggestions_and_passes():
  # Zero deltas: the projection round-trip is the identity, so vision == radar.
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
  assert result["d_yaw_rad"] == pytest.approx(math.sin(math.radians(0.3)), rel=0.10)


def test_fit_reports_p95_improvement():
  pairs = _synth_pairs(d_front=0.5, d_pitch=math.radians(0.1), d_yaw=math.radians(0.5))
  result = fit_calibrated_offsets(pairs)
  assert result["residual_p95_before_m"] > 1.0
  assert result["residual_p95_after_m"] < RESIDUAL_PASS_M


def test_format_constants_block_applies_increments():
  result = fit_calibrated_offsets(_synth_pairs(d_front=0.3, d_pitch=math.radians(0.1), d_yaw=math.radians(0.3)))
  block = format_constants_block(result)
  assert f"CAMERA_TO_FRONT = {C.CAMERA_TO_FRONT + result['d_front_m']:.4f}" in block
  assert f"CAMERA_PITCH = {C.CAMERA_PITCH + result['d_pitch_rad']:.6f}" in block
  assert f"CAMERA_YAW = {C.CAMERA_YAW + result['d_yaw_rad']:.6f}" in block


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
