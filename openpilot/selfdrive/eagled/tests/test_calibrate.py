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

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.calibrate import (BANDS, BEARING_PASS_DEG, CalibPair, MIN_FIT_PAIRS,
                                                      MIN_SAVE_PAIRS, banded_residuals, extract_pairs,
                                                      fit_calibrated_offsets, main, propose_camera_to_front)
from openpilot.selfdrive.eagled.projection import project_box_to_vehicle

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
                 noise=0.0, n=200, seed=0, current=None, true=None, distances=None):
  """Pairs whose vision side comes from the real projection round-trip.

  ``current`` is the (ctf, pitch, yaw) triple the projection runs with; the
  pixels are generated with the TRUE mount — by default ``current + deltas``,
  or the explicit ``true`` triple (pass it to keep the mount fixed across
  fit/apply iterations). ``distances`` overrides the default uniform 5-45 m
  spread with an explicit distance list (one pair per entry).
  """
  current = current if current is not None else (C.CAMERA_TO_FRONT, C.CAMERA_PITCH, C.CAMERA_YAW)
  true = true if true is not None else (current[0] + d_front, current[1] + d_pitch, current[2] + d_yaw)
  rng = np.random.default_rng(seed)
  pairs = []
  ds = rng.uniform(5.0, 45.0, n) if distances is None else np.asarray(distances, dtype=float)
  for d, y in zip(ds, rng.uniform(-2.0, 2.0, len(ds)), strict=True):
    u, v = _pixel_at(float(d), float(y), *true)
    vis = project_box_to_vehicle(u=u, v=v, fx=FX, fy=FY, cx=CX, cy=CY, height=C.CAMERA_HEIGHT,
                                 pitch=current[1], yaw=current[2], camera_to_front=current[0])
    pairs.append(CalibPair(d_radar=float(d), y_radar=float(y),
                           d_vision=vis["dRel"] + rng.normal(0.0, noise),
                           y_vision=vis["yRel"] + lateral_bias + rng.normal(0.0, noise),
                           v_ego=20.0))
  return pairs


# --- fit ----------------------------------------------------------------------
# Task 7: only CAMERA_TO_FRONT is hand-fitted (basis [1]); pitch/yaw/roll are
# openpilot's extrinsicsCalibration's job and appear only as contamination
# warnings. A pure constant offset round-trips exactly; the round-trip
# convergence test below pins the end-to-end behaviour.

def test_fit_recovers_injected_constants():
  pairs = _synth_pairs(d_front=0.3)
  result = fit_calibrated_offsets(pairs)
  assert result["d_front_m"] == pytest.approx(0.3, abs=0.06)
  assert "d_pitch_rad" not in result
  assert "d_yaw_rad" not in result
  assert result["forward_p95_after_m"] < 0.1
  assert result["pass"] is True
  assert result["warnings"] == []


def test_fit_recovers_injected_constants_with_noise():
  pairs = _synth_pairs(n=400, d_front=0.3, noise=0.03, seed=42)
  result = fit_calibrated_offsets(pairs)
  assert result["d_front_m"] == pytest.approx(0.3, abs=0.06)


def test_fit_flags_yaw_contamination_without_fitting_it():
  # Reviewer's numeric case: a camera truly yawed LEFT by +0.05 rad relative to
  # the live-calibrated geometry produces e_y slope = -0.04998 (= -sin 0.05,
  # verified against project_box_to_vehicle). The old fit turned that into a
  # CAMERA_YAW suggestion; now it must only WARN (yaw is extrinsicsCalibration's
  # job) and the constant lateral bias must stay ~0 instead of absorbing the slope.
  pairs = _synth_pairs(d_yaw=0.05, n=300, seed=7)
  result = fit_calibrated_offsets(pairs)
  assert "d_yaw_rad" not in result
  assert any("yaw" in w.lower() for w in result["warnings"])
  # The intercept keeps the Δyaw·CAMERA_TO_FRONT cross term (known aliasing),
  # NOT the distance-growing slope: |bias| stays below the mount-offset warning.
  assert result["lateral_bias_m"] == pytest.approx(-math.sin(0.05) * C.CAMERA_TO_FRONT, abs=0.02)


def test_roundtrip_convergence_two_iterations():
  # Only CAMERA_TO_FRONT is hand-fitted now; with pitch/yaw owned by live
  # calibration (and correct in this round-trip), applying the suggestion and
  # re-fitting must collapse the residual in one round and stay there. The true
  # mount stays FIXED while the compiled constant converges onto it.
  base = (C.CAMERA_TO_FRONT, C.CAMERA_PITCH, C.CAMERA_YAW)
  true = (base[0] + 0.3, base[1], base[2])
  current = list(base)
  p95s = []
  for _ in range(3):
    pairs = _synth_pairs(n=300, seed=7, current=tuple(current), true=true)
    result = fit_calibrated_offsets(pairs)
    p95s.append(result["forward_p95_before_m"])
    current = [current[0] + result["d_front_m"], current[1], current[2]]
  assert p95s[0] > p95s[1]
  assert p95s[2] < 0.1


def test_fit_zero_residual_gives_zero_suggestions_and_passes():
  # Zero deltas: the projection round-trip is the identity, so vision == radar.
  result = fit_calibrated_offsets(_synth_pairs())
  assert result["d_front_m"] == pytest.approx(0.0, abs=1e-9)
  assert result["forward_p95_after_m"] == pytest.approx(0.0, abs=1e-9)
  assert result["pass"] is True


def test_fit_too_few_pairs_raises():
  with pytest.raises(ValueError, match="pairs"):
    fit_calibrated_offsets(_synth_pairs(n=MIN_FIT_PAIRS - 1))


def test_fit_reports_lateral_bias_as_warning_without_absorbing_it():
  # A constant lateral residual must surface as a warning; with no yaw fit the
  # [1, d] regression is diagnostic only, so the distance-growing yaw term must
  # not eat the constant intercept either.
  pairs = _synth_pairs(d_yaw=math.radians(0.3), lateral_bias=0.15)
  result = fit_calibrated_offsets(pairs)
  assert result["lateral_bias_m"] == pytest.approx(0.15, abs=0.01)
  assert any("lateral mount" in w for w in result["warnings"])
  assert "d_yaw_rad" not in result
  assert any("yaw" in w.lower() for w in result["warnings"])


def test_fit_reports_p95_improvement():
  pairs = _synth_pairs(d_front=1.5)
  result = fit_calibrated_offsets(pairs)
  assert result["forward_p95_before_m"] > 1.0
  assert result["forward_p95_after_m"] < 0.1


# --- 保存防呆（票 #7）：建议值 = 当前值 + d_front（增量语义），越界/样本不足拒绝 ---

def test_min_save_pairs_is_thirty():
  assert MIN_SAVE_PAIRS == 30


def test_propose_adds_fitted_increment_to_current_value():
  proposal = propose_camera_to_front({"n_pairs": 40, "d_front_m": 0.3}, current=1.5)
  assert proposal["current_m"] == 1.5
  assert proposal["proposed_m"] == pytest.approx(1.8)
  assert proposal["savable"] is True
  assert proposal["reject_reason"] is None


def test_propose_rejects_too_few_pairs():
  proposal = propose_camera_to_front({"n_pairs": 29, "d_front_m": 0.1}, current=1.5)
  assert proposal["savable"] is False
  assert "29" in proposal["reject_reason"]
  assert proposal["proposed_m"] == pytest.approx(1.6)  # 仍展示，但不许存


@pytest.mark.parametrize("current,d_front", [(2.4, 0.3), (0.6, -0.2)])
def test_propose_rejects_value_outside_physical_range(current, d_front):
  proposal = propose_camera_to_front({"n_pairs": 100, "d_front_m": d_front}, current=current)
  assert proposal["savable"] is False
  assert "0.5" in proposal["reject_reason"] and "2.5" in proposal["reject_reason"]


# --- banded residuals (Task 7) --------------------------------------------------

def test_fit_no_longer_reports_pitch_or_yaw():
  """Task 2 之后 pitch/yaw 由 extrinsicsCalibration 提供, 手工拟合它们会与
  在线标定打架。只剩 CAMERA_TO_FRONT 需要手工标。"""
  pairs = _synth_pairs(d_front=0.4)          # 本文件 :39 已有的 helper
  result = fit_calibrated_offsets(pairs)
  assert "d_front_m" in result
  assert "d_pitch_rad" not in result
  assert "d_yaw_rad" not in result
  assert result["d_front_m"] == pytest.approx(0.4, abs=0.05)


def _pairs_across_distances():
  """每个距离档至少 3 对, 用于分档残差报告。"""
  out = []
  for d in (5.0, 7.0, 9.0, 12.0, 18.0, 24.0, 28.0, 34.0, 39.0):
    out.extend(_synth_pairs(d_front=0.0, distances=(d,)))
  return out


def test_banded_residuals_report_each_distance_band():
  bands = banded_residuals(_pairs_across_distances())
  assert set(bands) == {"le10m", "10to25m", "25to40m"}
  assert "p95_m" in bands["le10m"]
  assert "bearing_p95_deg" in bands["25to40m"]


def test_banded_residuals_marks_an_empty_band():
  bands = banded_residuals(_synth_pairs(distances=(5.0, 6.0)))
  assert bands["le10m"]["n"] > 0
  assert bands["25to40m"]["n"] == 0
  assert bands["25to40m"]["pass"] is None


def test_pass_uses_banded_thresholds():
  bands = banded_residuals(_pairs_across_distances())
  assert isinstance(bands["le10m"]["pass"], bool)


def test_fit_all_pairs_beyond_last_band_is_not_a_pass():
  """零 graded 样本不得读作 pass:所有配对都落在最后一个距离档之外时,每个档
  都是空的(pass=None),聚合判定绝不能因为"没有档位失败"而变绿 —— 那正是
  分档 None 规则要防止的失效在聚合层复活。"""
  pairs = _synth_pairs(distances=(41.0, 42.0, 43.0, 44.0, 45.0))
  result = fit_calibrated_offsets(pairs)
  assert all(entry["n"] == 0 for entry in result["bands"].values())
  assert result["pass"] is False


def test_fit_failing_populated_band_is_not_a_pass():
  """一个有人口且超阈值的档位必须直接判 FAIL(此前只经 main 退出码间接覆盖)。"""
  # Unmodelable noise survives the CAMERA_TO_FRONT correction, so the populated
  # le10m band's distance p95 exceeds its 0.40 m threshold post-correction.
  pairs = _synth_pairs(distances=(5.0, 6.0, 7.0, 8.0, 9.0), noise=0.5, seed=3)
  result = fit_calibrated_offsets(pairs)
  assert result["bands"]["le10m"]["n"] > 0
  assert result["bands"]["le10m"]["pass"] is False
  assert result["pass"] is False


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
  """Yields one pre-built eagleDebug frame per update() call."""

  def __init__(self, frames):
    self._frames = list(frames)
    self.updated = {}

  def update(self, timeout):
    if self._frames:
      self._current = self._frames.pop(0)
      self.updated = {"eagleDebug": True}
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


class _FakeParams:
  def __init__(self, data=None):
    self.data = dict(data or {})
    self.puts = []

  def get(self, key):
    return self.data.get(key)

  def put(self, key, value, block=False):
    self.puts.append((key, value, block))
    self.data[key] = value


def _offset_frames(n, e_d, d0=10.0):
  """n 帧、每帧一对，视觉 dRel 比雷达远恒定 e_d（= 安装偏移少计了 e_d）。"""
  frames = []
  for i in range(n):
    d = d0 + i * 0.5
    targets = [_target(d, -1.0, pair_id=1, vision=False), _target(d + e_d, -1.0, pair_id=1, vision=True)]
    frames.append(SimpleNamespace(targets=targets, vEgo=20.0))
  return frames


def test_main_insufficient_pairs_exits_1():
  sm = _FakeSM([_debug_frame([(20.0, -1.0)])])  # 1 pair < min 30
  params = _FakeParams()
  code = main(["--duration", "10", "--min-pairs", "30"], sm_factory=lambda: sm,
              clock=_FakeClock(), sleep=lambda s: None, params=params)
  assert code == 1
  assert params.puts == []


def test_main_collects_fits_and_passes_on_clean_data():
  frames = [_debug_frame([(10.0 + i * 0.5, -1.0)]) for i in range(40)]
  sm = _FakeSM(frames)
  code = main(["--duration", "10", "--min-pairs", "30", "--max-pairs", "500"],
              sm_factory=lambda: sm, clock=_FakeClock(), sleep=lambda s: None, params=_FakeParams())
  assert code == 0  # zero residual -> p95 0.0 -> pass


def test_main_saves_incremented_value_to_params_when_guard_passes():
  # 当前已存 1.6，采集到恒定 +0.2m 纵向残差 → 写 1.8（增量，不从出厂默认重算）
  params = _FakeParams({"CameraToFront": 1.6})
  sm = _FakeSM(_offset_frames(40, 0.2))
  main(["--duration", "100", "--min-pairs", "30"], sm_factory=lambda: sm,
       clock=_FakeClock(), sleep=lambda s: None, params=params)
  assert len(params.puts) == 1
  key, value, block = params.puts[0]
  assert (key, block) == ("CameraToFront", True)
  assert value == pytest.approx(1.8)


def test_main_refuses_to_save_out_of_range_value():
  params = _FakeParams({"CameraToFront": 2.4})
  sm = _FakeSM(_offset_frames(40, 0.3))  # 2.4 + 0.3 = 2.7 > 2.5
  main(["--duration", "100", "--min-pairs", "30"], sm_factory=lambda: sm,
       clock=_FakeClock(), sleep=lambda s: None, params=params)
  assert params.puts == []


def test_main_refuses_to_save_with_fewer_than_min_save_pairs():
  params = _FakeParams()
  sm = _FakeSM(_offset_frames(10, 0.2))  # 10 对：够 --min-pairs 5 拟合，不够 30 保存
  main(["--duration", "100", "--min-pairs", "5"], sm_factory=lambda: sm,
       clock=_FakeClock(), sleep=lambda s: None, params=params)
  assert params.puts == []


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
              clock=_FakeClock(), sleep=lambda s: None, params=_FakeParams())
  assert code == 1


def test_residual_thresholds_match_banded_spec():
  # Task 7: the single global 0.30 m threshold is gone (physically unreachable
  # beyond ~10 m); grading is per distance band, bearing-only past 25 m.
  assert BANDS == (("le10m", 0.0, 10.0, 0.40), ("10to25m", 10.0, 25.0, 1.20),
                   ("25to40m", 25.0, 40.0, None))
  assert BEARING_PASS_DEG == pytest.approx(0.6)
