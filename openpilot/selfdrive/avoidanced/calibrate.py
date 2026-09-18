"""Online calibration collector for the camera->car-frame projection constants.

The radar is the metric ground truth in the car frame (factory calibrated). The
``avoidanceDebug`` stream publishes, for every associated object, BOTH sources of
its position under a shared ``pairId``: the radar point (``vision=False``) and
the YOLO ground-plane projection (``vision=True``). The residual
``vision - radar`` therefore decomposes into the three projection-constant
errors this tool fits:

* forward residual ``e_d = d_vis - d_radar`` regressed on ``[1, d_r^2/h]`` ->
  ``CAMERA_TO_FRONT`` increment (constant shift) and ``CAMERA_PITCH`` increment
  (error growing with distance squared over camera height);
* lateral residual ``e_y = y_vis - y_radar`` regressed on ``[1, d_r]`` -> the
  slope is ``-Δyaw`` (the projection UNDOES the camera yaw: a camera truly
  yawed left by Δyaw relative to the compiled constant maps forward distance
  to ``e_y = -Δyaw·d``), so ``CAMERA_YAW += -slope``. The intercept is a
  constant component reported as a lateral mount-offset WARNING but never
  auto-assigned to a constant (it needs a physical re-measure, not a
  regression).

Iteration semantics: the vision coordinates already include the constants
currently compiled into ``constants.py``, so the fitted deltas are INCREMENTS to
add (``CAMERA_TO_FRONT += d_front`` etc.). Re-run after pasting; 1-2 rounds
converge because the model is linear in the constants.

Usage (on the device, with ``AvoidanceEnabled`` on and real traffic ahead)::

    python -m openpilot.selfdrive.avoidanced.calibrate [--duration 120] [--min-pairs 30] [--max-pairs 500]

Exit codes: 0 = fit pass (post-correction p95 residual < ``RESIDUAL_PASS_M``),
1 = insufficient pairs or fit out of tolerance.
"""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from openpilot.selfdrive.avoidanced import constants as C

if TYPE_CHECKING:
  import openpilot.cereal.messaging as messaging

# Mirrors shadow.CALIB_MAX_RESIDUAL_M (spec §4: 0.3 m is the unacceptable
# residual). Kept local so this collector stays import-light and standalone.
RESIDUAL_PASS_M = 0.30
# Minimum pairs for a meaningful fit: 2 forward basis coefficients + 1 yaw.
MIN_FIT_PAIRS = 3
# Constant lateral residual above this suggests a lateral mount offset (camera
# or radar origin sideways of the other) — reported, never auto-corrected.
LATERAL_BIAS_WARN_M = 0.10


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


def fit_calibrated_offsets(pairs: Sequence[CalibPair], height: float = C.CAMERA_HEIGHT) -> dict:
  """Least-squares fit of the three projection-constant increments from paired residuals.

  Regression bases (see module docstring for the physics):

  * ``e_d = d_vis - d_radar`` on ``[1, d_r^2/height]`` -> ``d_front_m`` (constant
    forward shift) and ``d_pitch_rad`` (distance-growing pitch error);
  * ``e_y = y_vis - y_radar`` on ``[1, d_r]`` -> ``d_yaw_rad`` is the NEGATED
    slope (slope = ``-Δyaw``, see module docstring); the intercept is
    ``lateral_bias_m`` (warning only, never folded into a constant).

  Raises ``ValueError`` with fewer than ``MIN_FIT_PAIRS`` pairs.
  """
  if len(pairs) < MIN_FIT_PAIRS:
    raise ValueError(f"need at least {MIN_FIT_PAIRS} pairs to fit, got {len(pairs)}")

  d_r = np.array([p.d_radar for p in pairs], dtype=float)
  e_d = np.array([p.d_vision - p.d_radar for p in pairs], dtype=float)
  e_y = np.array([p.y_vision - p.y_radar for p in pairs], dtype=float)

  warnings: list[str] = []

  # Forward residuals: constant term + pitch term growing as d^2/h.
  basis_d = np.column_stack([np.ones_like(d_r), d_r ** 2 / height])
  coeffs_d, *_ = np.linalg.lstsq(basis_d, e_d, rcond=None)
  d_front, d_pitch = float(coeffs_d[0]), float(coeffs_d[1])
  resid_d = e_d - basis_d @ coeffs_d
  if np.linalg.matrix_rank(basis_d) < 2:
    warnings.append("distance range too narrow to separate CAMERA_TO_FRONT from CAMERA_PITCH; collect pairs across a wider distance spread")
  # Lateral residuals: yaw term growing as d. The projection UNDOES the camera
  # yaw (vehicle = R(yaw)·camera), so a camera truly yawed left by Δyaw relative
  # to the compiled constant produces e_y = -Δyaw·d: the regression slope is the
  # NEGATIVE of the increment to apply. The intercept column separates a
  # constant mount offset from the yaw term; only the negated slope is suggested
  # as a constant, the intercept is warning-only.
  basis_y = np.column_stack([np.ones_like(d_r), d_r])
  coeffs_y, *_ = np.linalg.lstsq(basis_y, e_y, rcond=None)
  lateral_bias = float(coeffs_y[0])
  d_yaw = -float(coeffs_y[1])  # slope = -Δyaw (see comment above)
  resid_y = e_y + d_yaw * d_r  # applying Δyaw raises y_vision by ~d·Δyaw, cancelling the residual
  lateral_warning = abs(lateral_bias) >= LATERAL_BIAS_WARN_M
  if lateral_warning:
    warnings.append(f"constant lateral residual {lateral_bias:+.3f} m suggests a lateral mount offset; re-measure physically, do not patch it into a constant")
  if np.allclose(d_r, 0.0):
    warnings.append("all pairs at zero distance; CAMERA_YAW is unidentifiable")

  fwd_p95_before, fwd_p95_after = _p95_abs(e_d), _p95_abs(resid_d)
  lat_p95_before, lat_p95_after = _p95_abs(e_y), _p95_abs(resid_y)
  p95_before = max(fwd_p95_before, lat_p95_before)
  p95_after = max(fwd_p95_after, lat_p95_after)

  return {
    "n_pairs": len(pairs),
    "v_ego_min": min(p.v_ego for p in pairs),
    "v_ego_max": max(p.v_ego for p in pairs),
    "d_front_m": d_front,
    "d_pitch_rad": d_pitch,
    "d_yaw_rad": d_yaw,
    "lateral_bias_m": lateral_bias,
    "forward_p95_before_m": fwd_p95_before,
    "forward_p95_after_m": fwd_p95_after,
    "lateral_p95_before_m": lat_p95_before,
    "lateral_p95_after_m": lat_p95_after,
    "residual_p95_before_m": p95_before,
    "residual_p95_after_m": p95_after,
    "warnings": warnings,
    "pass": p95_after <= RESIDUAL_PASS_M,
  }


def format_constants_block(result: dict) -> str:
  """Paste-ready constants.py block with the suggested increments applied."""
  new_front = C.CAMERA_TO_FRONT + result["d_front_m"]
  new_pitch = C.CAMERA_PITCH + result["d_pitch_rad"]
  new_yaw = C.CAMERA_YAW + result["d_yaw_rad"]
  lines = [
    f"# Fit from {result['n_pairs']} pairs, post-correction p95 residual {result['residual_p95_after_m']:.3f} m.",
    "# Deltas are increments: vision coords already include the current constants,",
    "# so re-run after pasting (1-2 rounds converge).",
    f"CAMERA_TO_FRONT = {new_front:.4f}  # was {C.CAMERA_TO_FRONT:.4f} ({result['d_front_m']:+.4f} m)",
    f"CAMERA_PITCH = {new_pitch:.6f}  # was {C.CAMERA_PITCH:.6f} ({result['d_pitch_rad']:+.6f} rad = {math.degrees(result['d_pitch_rad']):+.3f} deg)",
    f"CAMERA_YAW = {new_yaw:.6f}  # was {C.CAMERA_YAW:.6f} ({result['d_yaw_rad']:+.6f} rad = {math.degrees(result['d_yaw_rad']):+.3f} deg)",
  ]
  return "\n".join(lines)


def _print_report(result: dict) -> None:
  print(f"pairs: {result['n_pairs']}  vEgo range: {result['v_ego_min']:.1f}-{result['v_ego_max']:.1f} m/s")
  print(f"forward residual p95: {result['forward_p95_before_m']:.3f} m -> {result['forward_p95_after_m']:.3f} m (corrected)")
  print(f"lateral residual p95: {result['lateral_p95_before_m']:.3f} m -> {result['lateral_p95_after_m']:.3f} m (corrected)")
  verdict = "PASS" if result["pass"] else "FAIL"
  p95 = result["residual_p95_after_m"]
  print(f"combined residual p95: {result['residual_p95_before_m']:.3f} m -> {p95:.3f} m ({verdict}, threshold {RESIDUAL_PASS_M:.2f} m)")
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
  parser = argparse.ArgumentParser(description="Online calibration collector for avoidanced projection constants.")
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
