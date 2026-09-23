"""Online calibration collector for the camera->car-frame projection constants.

The radar is the metric ground truth in the car frame (factory calibrated). The
``avoidanceDebug`` stream publishes, for every associated object, BOTH sources of
its position under a shared ``pairId``: the radar point (``vision=False``) and
the YOLO ground-plane projection (``vision=True``).

Since Task 2 the projection takes pitch/yaw/roll from openpilot's live
``extrinsicsCalibration`` (``projection.CalibratedGeometry``) and no longer
reads ``CAMERA_PITCH``/``CAMERA_YAW`` — so this tool fits ONLY what live
calibration cannot provide: the longitudinal camera->front-bumper mount offset.
The forward residual ``e_d = d_vis - d_radar`` is regressed on ``[1]`` (constant
only) -> ``CAMERA_TO_FRONT += d_front_m``. Fitting Δpitch/Δyaw here would hand
the operator numbers nothing consumes, and invite "correcting" a calibration
openpilot maintains continuously.

Everything else is DIAGNOSTIC, reported but never folded into a constant:

* constant lateral residual ``e_y`` intercept -> lateral mount offset (physical
  re-measure needed);
* distance-growing forward residual -> pitch error, distance-growing lateral
  residual -> yaw error: both are ``extrinsicsCalibration``'s job — re-collect
  after it reports ``calibrated``;
* :func:`banded_residuals` grades the residuals per distance band (a single
  global threshold is physically unreachable beyond ~10 m: the ground-plane
  projection's dRel sensitivity to pitch would demand 0.013 deg at 40 m while
  vehicle pitch alone swings ~1 deg under braking).

Usage (on the device, with ``AvoidanceEnabled`` on and real traffic ahead)::

    python -m openpilot.selfdrive.eagled.calibrate [--duration 120] [--min-pairs 30] [--max-pairs 500]

Exit codes: 0 = banded residual verdict pass, 1 = insufficient pairs or a
populated band out of tolerance.
"""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from openpilot.selfdrive.eagled import constants as C

if TYPE_CHECKING:
  import openpilot.cereal.messaging as messaging

# Banded residual criteria (Task 7). A single global threshold (the old
# RESIDUAL_PASS_M = 0.30 m) is unreachable beyond ~10 m: the ground-plane
# projection's dRel sensitivity to pitch demands 0.203 deg pitch accuracy at
# 10 m but 0.013 deg at 40 m, while vehicle pitch swings ~1 deg under braking
# and openpilot's own calibration lands in the 0.1-0.2 deg range. Beyond 25 m
# only the bearing residual is graded — bearing is what a monocular camera
# actually measures well (independent of pitch and the ground-plane assumption).
BANDS = (("le10m", 0.0, 10.0, 0.40), ("10to25m", 10.0, 25.0, 1.20),
         ("25to40m", 25.0, 40.0, None))
BEARING_PASS_DEG = 0.6
# Minimum pairs for a meaningful fit: 1 fitted constant + statistical floor.
MIN_FIT_PAIRS = 3
# Constant lateral residual above this suggests a lateral mount offset (camera
# or radar origin sideways of the other) — reported, never auto-corrected.
LATERAL_BIAS_WARN_M = 0.10
# Live calibration owns pitch/yaw/roll and lands around 0.1-0.2 deg; a residual
# structure equivalent to more than this means the collected pairs still carry
# mount error that NO constant in constants.py can fix (the projection no
# longer reads CAMERA_PITCH/CAMERA_YAW) — warn instead of suggesting one.
CONTAMINATION_WARN_RAD = math.radians(0.2)


@dataclass(frozen=True)
class CalibPair:
  """One physical object seen by both sensors in the same 5Hz frame."""

  d_radar: float   # m, radar dRel (front-bumper origin, metric truth)
  y_radar: float   # m, radar yRel (left positive)
  d_vision: float  # m, projected YOLO dRel (includes current constants)
  y_vision: float  # m, projected YOLO yRel (includes current constants)
  v_ego: float     # m/s, for the speed-range report


def extract_pairs(targets: Sequence[Any], v_ego: float) -> list[CalibPair]:
  """One :class:`CalibPair` per shared non-zero ``pairId`` with exactly one side per sensor.

  Unpaired targets (``pairId == 0``) and degenerate groups (two radar points on
  one detection, or a pairId with only one side this frame) are skipped: the
  regression needs unambiguous same-object positions.
  """
  by_id: dict[int, list[Any]] = {}
  for target in targets:
    pair_id = int(target.pairId)
    if pair_id == 0:
      continue
    by_id.setdefault(pair_id, []).append(target)

  pairs: list[CalibPair] = []
  for members in by_id.values():
    radar = [t for t in members if not t.vision]
    vision = [t for t in members if t.vision]
    if len(radar) == 1 and len(vision) == 1:
      pairs.append(CalibPair(d_radar=float(radar[0].dRel), y_radar=float(radar[0].yRel),
                             d_vision=float(vision[0].dRel), y_vision=float(vision[0].yRel),
                             v_ego=float(v_ego)))
  return pairs


def _p95_abs(values: np.ndarray) -> float:
  return float(np.percentile(np.abs(values), 95.0))


def banded_residuals(pairs: Sequence[CalibPair]) -> dict:
  """按距离分档的残差报告。

  单一全局阈值(旧的 RESIDUAL_PASS_M)在 10m 以外物理上不可达:地平面投影的
  dRel 对 pitch 的敏感度使 p95<0.30m 在 40m 处要求 0.013deg 的 pitch 精度,
  而车辆俯仰变化就有 1deg 量级。25m 以上因此只考核方位角残差 —— 那是单目真正
  测得准的量。

  空档报告 ``pass: None``(不是 pass):没有数据的档位不能默认读作通过,
  否则门限就失去了把关作用。
  """
  out: dict = {}
  for name, lo, hi, thresh in BANDS:
    sel = [p for p in pairs if lo <= p.d_radar < hi]
    if not sel:
      out[name] = {"n": 0, "pass": None}
      continue
    e_d = np.array([p.d_vision - p.d_radar for p in sel], dtype=float)
    b_err = np.array([math.degrees(math.atan2(-p.y_vision, p.d_vision)
                                   - math.atan2(-p.y_radar, p.d_radar)) for p in sel])
    entry = {"n": len(sel), "p95_m": _p95_abs(e_d),
             "bearing_p95_deg": _p95_abs(b_err)}
    entry["pass"] = (entry["bearing_p95_deg"] <= BEARING_PASS_DEG) if thresh is None \
      else (entry["p95_m"] <= thresh and entry["bearing_p95_deg"] <= BEARING_PASS_DEG)
    out[name] = entry
  return out


def fit_calibrated_offsets(pairs: Sequence[CalibPair], height: float = C.CAMERA_HEIGHT) -> dict:
  """Least-squares fit of the CAMERA_TO_FRONT increment from paired residuals.

  Pitch/yaw/roll are openpilot's job (``extrinsicsCalibration`` ->
  ``projection.CalibratedGeometry``): the projection no longer reads
  ``CAMERA_PITCH``/``CAMERA_YAW``, so they are NOT fitted here. The forward
  basis is ``[1]`` — the single fitted quantity is ``d_front_m``
  (``CAMERA_TO_FRONT += d_front_m``), the one mount constant live calibration
  does not provide.

  Diagnostics (reported, never fitted into constants):

  * ``lateral_bias_m`` — constant lateral residual (intercept of the ``[1, d]``
    regression): a physical lateral mount offset; re-measure, never patch.
  * distance-growing forward residual -> Δpitch, distance-growing lateral
    residual -> Δyaw (slope = -Δyaw, the projection undoes camera yaw): both
    are ``extrinsicsCalibration``'s job; flagged as warnings above
    ``CONTAMINATION_WARN_RAD``.
  * ``bands`` — :func:`banded_residuals` over the POST-correction pairs (the
    fitted ``CAMERA_TO_FRONT`` increment applied); ``pass`` is the
    banded verdict (a populated band failing fails the fit; empty bands report
    ``pass: None`` and do not fail it, but are listed in the warnings).

  Raises ``ValueError`` with fewer than ``MIN_FIT_PAIRS`` pairs.
  """
  if len(pairs) < MIN_FIT_PAIRS:
    raise ValueError(f"need at least {MIN_FIT_PAIRS} pairs to fit, got {len(pairs)}")

  d_r = np.array([p.d_radar for p in pairs], dtype=float)
  e_d = np.array([p.d_vision - p.d_radar for p in pairs], dtype=float)
  e_y = np.array([p.y_vision - p.y_radar for p in pairs], dtype=float)

  warnings: list[str] = []

  # Forward residuals: constant term only (= the CAMERA_TO_FRONT increment).
  # Pitch is live calibration's job — the old [1, d²/h] pitch basis is kept
  # ONLY as a contamination diagnostic below, never fitted into a constant.
  d_front = float(np.mean(e_d))
  resid_d = e_d - d_front
  basis_d = np.column_stack([np.ones_like(d_r), d_r ** 2 / height])
  coeffs_d, *_ = np.linalg.lstsq(basis_d, e_d, rcond=None)
  d_pitch_equiv = float(coeffs_d[1])
  if abs(d_pitch_equiv) > CONTAMINATION_WARN_RAD:
    warnings.append(f"forward residual grows with distance (Δpitch ≈ {math.degrees(d_pitch_equiv):+.2f} deg); "
                    "pitch is extrinsicsCalibration's job — re-collect after it reports calibrated")
  if np.linalg.matrix_rank(basis_d) < 2:
    warnings.append("distance range too narrow to diagnose pitch contamination; collect pairs across a wider distance spread")

  # Lateral residuals are diagnostic only: the projection takes yaw from live
  # calibration, so there is nothing to paste into CAMERA_YAW. The [1, d]
  # regression still separates a constant mount offset (intercept -> warning)
  # from a distance-growing yaw error (slope -> contamination warning). The
  # projection UNDOES the camera yaw (vehicle = R(yaw)·camera), so a camera
  # truly yawed left by Δyaw produces e_y = -Δyaw·d: the slope is the NEGATIVE
  # of the equivalent yaw error.
  basis_y = np.column_stack([np.ones_like(d_r), d_r])
  coeffs_y, *_ = np.linalg.lstsq(basis_y, e_y, rcond=None)
  lateral_bias = float(coeffs_y[0])
  d_yaw_equiv = -float(coeffs_y[1])  # slope = -Δyaw (see comment above)
  lateral_warning = abs(lateral_bias) >= LATERAL_BIAS_WARN_M
  if lateral_warning:
    warnings.append(f"constant lateral residual {lateral_bias:+.3f} m suggests a lateral mount offset; re-measure physically, do not patch it into a constant")
  if abs(d_yaw_equiv) > CONTAMINATION_WARN_RAD:
    warnings.append(f"lateral residual grows with distance (Δyaw ≈ {math.degrees(d_yaw_equiv):+.2f} deg); "
                    "yaw is extrinsicsCalibration's job — re-collect after it reports calibrated")
  if np.allclose(d_r, 0.0):
    warnings.append("all pairs at zero distance; yaw contamination is unidentifiable")

  fwd_p95_before, fwd_p95_after = _p95_abs(e_d), _p95_abs(resid_d)
  lat_p95 = _p95_abs(e_y)

  # Bands grade the POST-correction residuals: the question the verdict answers
  # is "after applying the fitted CAMERA_TO_FRONT increment, does the perception
  # layer meet the banded criteria?" (grading raw pairs would fail the le10m
  # bearing gate on the y·Δd/d² tilt a pure CAMERA_TO_FRONT offset causes).
  corrected = [CalibPair(d_radar=p.d_radar, y_radar=p.y_radar,
                         d_vision=p.d_vision - d_front, y_vision=p.y_vision,
                         v_ego=p.v_ego) for p in pairs]
  bands = banded_residuals(corrected)
  empty_bands = [name for name, entry in bands.items() if entry["n"] == 0]
  if empty_bands:
    warnings.append(f"no pairs in distance band(s) {', '.join(empty_bands)}; coverage incomplete")
  # The verdict needs at least one POPULATED band that passed, not merely "no
  # band failed": an all-empty dataset (e.g. every pair beyond 40 m) grades
  # zero samples, and a gate that returns "passed" without grading anything is
  # not a gate — that is exactly the spurious-pass failure the per-band
  # `pass: None` rule exists to prevent, so it must not reappear one level up
  # as all(None) -> True. Hence any(True) AND not any(False), not all(not False).
  populated_passed = any(entry["pass"] is True for entry in bands.values())
  any_failed = any(entry["pass"] is False for entry in bands.values())

  return {
    "n_pairs": len(pairs),
    "v_ego_min": min(p.v_ego for p in pairs),
    "v_ego_max": max(p.v_ego for p in pairs),
    "d_front_m": d_front,
    "lateral_bias_m": lateral_bias,
    "forward_p95_before_m": fwd_p95_before,
    "forward_p95_after_m": fwd_p95_after,
    "lateral_p95_m": lat_p95,
    # Combined p95 kept for the lanlink calibration UI (renders before -> after):
    # the fit only corrects the forward constant, so the uncorrected lateral
    # residual appears in both.
    "residual_p95_before_m": max(fwd_p95_before, lat_p95),
    "residual_p95_after_m": max(fwd_p95_after, lat_p95),
    "bands": bands,
    "warnings": warnings,
    "pass": populated_passed and not any_failed,
  }


def format_constants_block(result: dict) -> str:
  """Paste-ready constants.py block with the suggested CAMERA_TO_FRONT increment.

  Only ``CAMERA_TO_FRONT`` is hand-fitted: pitch/yaw/roll are maintained by
  openpilot's ``extrinsicsCalibration`` and the projection no longer reads
  ``CAMERA_PITCH``/``CAMERA_YAW``, so no pitch/yaw lines are printed.
  """
  new_front = C.CAMERA_TO_FRONT + result["d_front_m"]
  lines = [
    f"# Fit from {result['n_pairs']} pairs, post-correction forward p95 {result['forward_p95_after_m']:.3f} m.",
    "# Only CAMERA_TO_FRONT is hand-fitted: pitch/yaw/roll come from openpilot's",
    "# extrinsicsCalibration and are no longer read by the projection — do not",
    "# patch them here.",
    f"CAMERA_TO_FRONT = {new_front:.4f}  # was {C.CAMERA_TO_FRONT:.4f} ({result['d_front_m']:+.4f} m)",
  ]
  return "\n".join(lines)


def _print_report(result: dict) -> None:
  print(f"pairs: {result['n_pairs']}  vEgo range: {result['v_ego_min']:.1f}-{result['v_ego_max']:.1f} m/s")
  print(f"forward residual p95: {result['forward_p95_before_m']:.3f} m -> {result['forward_p95_after_m']:.3f} m (CAMERA_TO_FRONT applied)")
  print(f"lateral residual p95: {result['lateral_p95_m']:.3f} m (report only; pitch/yaw are extrinsicsCalibration's job)")
  print("banded residuals:")
  for name, entry in result["bands"].items():
    if entry["n"] == 0:
      print(f"  {name:8s}  no pairs (pass: None)")
      continue
    verdict = "pass" if entry["pass"] else "FAIL"
    print(f"  {name:8s}  n={entry['n']:3d}  distance p95 {entry['p95_m']:.3f} m  bearing p95 {entry['bearing_p95_deg']:.2f} deg  -> {verdict}")
  verdict = "PASS" if result["pass"] else "FAIL"
  print(f"verdict: {verdict} (banded)")
  for warning in result["warnings"]:
    print(f"WARNING: {warning}")


def collect_pairs(sm: messaging.SubMaster, duration: float, max_pairs: int,
                  clock=time.monotonic, sleep=time.sleep) -> list[CalibPair]:
  """Collect paired radar/vision positions from ``avoidanceDebug`` for ``duration`` seconds."""
  pairs: list[CalibPair] = []
  start = clock()
  last_progress = start
  while True:
    now = clock()
    if now - start >= duration or len(pairs) >= max_pairs:
      break
    sm.update(0)
    if sm.updated.get("avoidanceDebug"):
      dbg = sm["avoidanceDebug"]
      pairs.extend(extract_pairs(dbg.targets, float(dbg.vEgo)))
    if now - last_progress >= 5.0:
      print(f"  {len(pairs)} pairs collected...", flush=True)
      last_progress = now
    sleep(0.02)
  return pairs


def main(argv: list[str] | None = None, sm_factory: Any = None,
         clock=time.monotonic, sleep=time.sleep) -> int:
  parser = argparse.ArgumentParser(description="Online calibration collector for eagled projection constants.")
  parser.add_argument("--duration", type=float, default=120.0, help="collection window in seconds")
  parser.add_argument("--min-pairs", type=int, default=30, help="minimum pairs required to fit")
  parser.add_argument("--max-pairs", type=int, default=500, help="stop collecting after this many pairs")
  args = parser.parse_args(argv)

  if sm_factory is None:
    import openpilot.cereal.messaging as messaging
    sm_factory = lambda: messaging.SubMaster(["avoidanceDebug"])  # noqa: E731

  print(f"Collecting paired targets for {args.duration:.0f} s (min {args.min_pairs}, max {args.max_pairs})...")
  pairs = collect_pairs(sm_factory(), args.duration, args.max_pairs, clock=clock, sleep=sleep)

  if len(pairs) < args.min_pairs:
    print(f"FAIL: only {len(pairs)} paired targets collected (minimum {args.min_pairs}).")
    print("Drive with AvoidanceEnabled on, following other vehicles at varied distances, then retry.")
    return 1

  result = fit_calibrated_offsets(pairs)
  _print_report(result)
  print(format_constants_block(result))
  return 0 if result["pass"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
