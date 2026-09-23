"""Decision-level lateral avoidance planner.

The planner consumes already-fused targets (radar points, optionally upgraded by
YOLO VRU detections) plus vehicle state and produces a curvature bias that is
added on top of the model curvature. It never commands a path of its own: the
consumer (``eagled``) republishes ``model + bias`` every frame and clears the
envelope ``valid`` flag when the plan is invalid, so control falls back to the
raw model curvature.

Fusion primitives (``RadarPoint``/``Target``/``_sign``/``_in_gate``/
``radar_point_key``/``fuse_targets``) live in ``perception`` — the perception
core of the eagle; they are re-exported here for import compatibility.

Sign convention: ``yRel`` is left-positive (car frame), so a target on the left
produces a negative ``y_des`` (avoid right) and vice-versa.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from openpilot.common.filter_simple import FirstOrderFilter

from openpilot.selfdrive.eagled.perception import (  # noqa: F401  (compat re-export)
  RadarPoint, Target, _in_gate, _sign, fuse_targets, radar_point_key,
)
from openpilot.selfdrive.eagled.constants import (D_MAX, DT_5HZ, EDGE_CLEAR_MIN, ENTER_HOLD_S, EXIT_HOLD_S,
                                                  K_GAIN, L_LOOKAHEAD, LOWPASS_TAU_S, MAX_OFFSET_BSM,
                                                  MAX_OFFSET_FREE, V_EGO_MAX, V_EGO_MIN)


def _best_target(targets: Iterable[Target], max_offset: float) -> tuple[Target, float] | None:
  """Highest-desire in-gate target, and its magnitude capped to ``max_offset``.

  Ranking uses the UNCAPPED desire on purpose. Ranking by the capped magnitude
  makes target selection depend on ``max_offset``: once BSM squeezes the cap to
  MAX_OFFSET_BSM most in-gate targets saturate at exactly that value, the strict
  ``>`` tie-break then keeps whichever came first in iteration order, and the
  selected target -- hence the avoidance side -- can flip. update() derives the
  BSM gates from the uncapped direction, so a flip there commands a bias toward
  a side whose blind spot was never checked.
  """
  if max_offset <= 0.0:
    return None
  best: tuple[Target, float] | None = None
  for target in targets:
    if not _in_gate(target.dRel, target.yRel):
      continue
    proximity = max(0.0, 1.0 - target.dRel / D_MAX)
    desire = K_GAIN * target.w * proximity
    if desire > 0.0 and (best is None or desire > best[1]):
      best = (target, desire)
  if best is None:
    return None
  return (best[0], min(max_offset, best[1]))


def _avoid_direction(targets: Iterable[Target], max_offset: float = MAX_OFFSET_FREE) -> int:
  best = _best_target(targets, max_offset)
  return 0 if best is None else -_sign(best[0].yRel)


def plan(targets: Iterable[Target], max_offset: float = MAX_OFFSET_FREE,
         bsm_opposite: bool = False, bsm_same: bool = False) -> float:
  """Desired lateral offset (m) for the nearest in-gate target, signed.

  ``bsm_same`` (blind-spot vehicle on the side the bias would move toward)
  forbids the bias; ``bsm_opposite`` caps it at ``MAX_OFFSET_BSM``.
  """
  targets = tuple(targets)
  if not targets or bsm_same:
    return 0.0
  if bsm_opposite:
    max_offset = min(max_offset, MAX_OFFSET_BSM)
  best = _best_target(targets, max_offset)
  if best is None:
    return 0.0
  return -_sign(best[0].yRel) * best[1]


def edge_clearance(road_edges: Iterable, side: int = 0) -> float:
  """Smallest |y| (m) of a road edge within the lookahead, ``inf`` if none.

  ``side`` is the bias direction (+1 = bias left, -1 = bias right, 0 = both).
  Frame convention (verified against upstream ``ldw.py``/``relc.py``):
  modelV2 y is **right-positive**, so the LEFT edge lies at negative y
  (roadEdges[0]) and the RIGHT edge at positive y (roadEdges[1]).
  """
  clearance = float("inf")
  for edge in road_edges or []:
    xs, ys = getattr(edge, "x", None), getattr(edge, "y", None)
    if xs is None or ys is None:
      continue
    for x, y in zip(xs, ys, strict=False):
      if x < 0.0 or x > L_LOOKAHEAD:
        continue
      if side > 0 and y >= 0.0:   # bias left -> keep LEFT edges (y < 0)
        continue
      if side < 0 and y <= 0.0:   # bias right -> keep RIGHT edges (y > 0)
        continue
      clearance = min(clearance, abs(float(y)))
  return clearance


class AvoidancePlanner:
  """Stateful wrapper adding low-pass smoothing and enter/exit hysteresis."""

  def __init__(self, clock=time.monotonic):
    self._clock = clock
    self.reset()

  def reset(self) -> None:
    self._bias = FirstOrderFilter(0.0, LOWPASS_TAU_S, DT_5HZ)
    self._active = False
    self._enter_since: float | None = None
    self._last_target_t: float | None = None
    # Per-frame snapshot of the planner's decision state (eagled reads it to
    # fill eagleDebug; every update() call refreshes it).
    self.last_state: dict = {}

  def update(self, model_curvature: float, targets: Iterable[Target], v_ego: float,
             bsm_left: bool = False, bsm_right: bool = False, road_edges: Iterable = (),
             enabled: bool = True, lat_active: bool = True, steering_pressed: bool = False,
             lane_change_active: bool = False,
             max_offset: float = MAX_OFFSET_FREE, now: float | None = None) -> tuple[float, bool]:
    """Return ``(desired_curvature, valid)`` for one 5Hz frame.

    ``valid=False`` means the caller must not trust the plan (publish the frame
    with the envelope ``valid`` flag cleared and the model curvature).

    The bias is suppressed while ``lane_change_active``: the model curvature is
    already executing a large lateral manoeuvre and the target's relative
    bearing is changing fast, so a bias derived from "target is on the
    left/right" stacked on top of it is unpredictable.
    """
    now = self._clock() if now is None else now
    targets = tuple(targets)

    direction = _avoid_direction(targets, max_offset)
    # Direction-aware road-edge gate: only the edge on the side the bias would
    # move toward can block the manoeuvre (spec §3 roadEdge clearance).
    clearance = edge_clearance(road_edges, side=direction)
    bsm_same = (direction > 0 and bsm_left) or (direction < 0 and bsm_right)
    bsm_opposite = (direction > 0 and bsm_right) or (direction < 0 and bsm_left)
    # Effective offset cap this frame: BSM on the opposite side caps it (the
    # same limit plan() applies internally).
    if bsm_opposite:
      max_offset = min(max_offset, MAX_OFFSET_BSM)
    y_des = plan(targets, max_offset=max_offset, bsm_opposite=bsm_opposite, bsm_same=bsm_same)

    # Hysteresis tracks target presence, not the (possibly BSM-suppressed) bias.
    has_target = _best_target(targets, max_offset) is not None
    if has_target:
      if self._enter_since is None:
        self._enter_since = now
      self._last_target_t = now
      if self._active or (now - self._enter_since) >= ENTER_HOLD_S:
        self._active = True
    else:
      self._enter_since = None
      if self._active and self._last_target_t is not None and (now - self._last_target_t) >= EXIT_HOLD_S:
        self._active = False

    gated = (enabled and lat_active and not steering_pressed
             and not lane_change_active
             and V_EGO_MIN <= v_ego <= V_EGO_MAX
             and clearance >= EDGE_CLEAR_MIN)
    if not (gated and self._active):
      bias = float(self._bias.update(0.0))
      self.last_state = {
        "active": self._active, "direction": direction, "yDes": 0.0, "bias": bias,
        "maxOffset": max_offset, "edgeClearance": clearance,
        "bsmLeft": bsm_left, "bsmRight": bsm_right, "vEgo": v_ego,
      }
      return float(model_curvature), False

    bias = self._bias.update(y_des)
    self.last_state = {
      "active": self._active, "direction": direction, "yDes": y_des, "bias": bias,
      "maxOffset": max_offset, "edgeClearance": clearance,
      "bsmLeft": bsm_left, "bsmRight": bsm_right, "vEgo": v_ego,
    }
    return float(model_curvature) + 2.0 * bias / L_LOOKAHEAD ** 2, True
