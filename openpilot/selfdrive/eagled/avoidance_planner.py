"""Decision-level lateral avoidance planner.

The planner consumes already-fused targets (radar points, optionally upgraded by
YOLO VRU detections) plus vehicle state and produces a curvature bias that is
added on top of the model curvature. It never commands a path of its own: the
consumer (``eagled``) republishes ``model + bias`` every frame and clears the
envelope ``valid`` flag when the plan is invalid, so control falls back to the
raw model curvature.

Fusion primitives (``RadarPoint``/``Target``/``_sign``/``_in_gate``/``radar_point_key``/
``fuse_targets``/``lane_geometry``/``gate_target``) live in ``perception`` —
the perception core of the eagle; they are re-exported here for import
compatibility.

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
from openpilot.selfdrive.eagled.constants import (BUDGET_UNCONSTRAINED, D_MAX, DT_5HZ, EDGE_CLEAR_MIN,
                                                  EDGE_STD_MAX, ENTER_HOLD_S, EXIT_HOLD_S, K_GAIN,
                                                  L_LOOKAHEAD, LOWPASS_TAU_S, MAX_OFFSET_FREE,
                                                  V_EGO_MAX, V_EGO_MIN)


def _best_target(targets: Iterable[Target], max_offset: float) -> tuple[Target, float] | None:
  """Highest-desire in-gate target, and its magnitude capped to ``max_offset``.

  Ranking uses the UNCAPPED desire on purpose. Ranking by the capped magnitude
  makes target selection depend on ``max_offset``: once BSM squeezes the cap to
  MAX_OFFSET_BSM most in-gate targets saturate at exactly that value, the strict
  ``>`` tie-break then keeps whichever came first in iteration order, and the
  selected target -- hence the avoidance side -- can flip. update() derives the
  BSM gates from the uncapped direction, so a flip there commands a bias toward
  a side whose blind spot was never checked.

  The in/out decision is ``target.in_gate`` — gate_target's three-tier verdict
  already applied by fuse_targets; re-deriving a geometry gate here would use
  the fixed band and silently disagree with lane-relative mode.
  """
  if max_offset <= 0.0:
    return None
  best: tuple[Target, float] | None = None
  for target in targets:
    if not target.in_gate:
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


def plan(targets: Iterable[Target], max_offset: float = MAX_OFFSET_FREE) -> float:
  """Desired lateral offset (m) for the nearest in-gate target, signed.

  ``max_offset`` must already carry the C9 budget verdict — update() folds
  ``budget_<bias side>`` into it before calling. plan() itself is budget-blind:
  it only ranks threats and caps by the offset it was given.
  """
  targets = tuple(targets)
  if not targets:
    return 0.0
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
             budget_left: float = None, budget_right: float = None, road_edges: Iterable = (),
             enabled: bool = True, lat_active: bool = True, steering_pressed: bool = False,
             lane_change_active: bool = False,
             max_offset: float = MAX_OFFSET_FREE, now: float | None = None,
             road_edge_stds=None) -> tuple[float, bool]:
    """Return ``(desired_curvature, valid)`` for one 5Hz frame.

    ``valid=False`` means the caller must not trust the plan (publish the frame
    with the envelope ``valid`` flag cleared and the model curvature).

    C9 budgets: ``budget_left``/``budget_right`` are the per-side lateral
    budgets computed by ``perception.side_pictures`` (BSM -> 0, side-object
    gap physics otherwise, BUDGET_UNCONSTRAINED when nothing constrains).
    ``None`` keeps the pre-C9 behavior of an unconstrained budget for tests
    and the shadow harness. The bias folds the budget of the side it moves
    TOWARD into max_offset; moving away from an occupied side is safe and no
    longer capped (the old discrete bsm_opposite 0.12 cap is superseded by the
    physics: gap to what we approach is what matters).

    The bias is suppressed while ``lane_change_active``: the model curvature is
    already executing a large lateral manoeuvre and the target's relative
    bearing is changing fast, so a bias derived from "target is on the
    left/right" stacked on top of it is unpredictable.
    """
    now = self._clock() if now is None else now
    targets = tuple(targets)
    budget_left = BUDGET_UNCONSTRAINED if budget_left is None else budget_left
    budget_right = BUDGET_UNCONSTRAINED if budget_right is None else budget_right

    direction = _avoid_direction(targets, max_offset)
    # Direction-aware road-edge gate: only the edge on the side the bias would
    # move toward can block the manoeuvre (spec §3 roadEdge clearance).
    clearance = edge_clearance(road_edges, side=direction)
    # C7 置信门:偏置侧路沿方差超标 -> 该侧视为无净空。保守方向:宁可错过
    # 避让,不可把车往看不清的边沿外推（净空被高估是危险失效方向）。
    if direction != 0 and road_edge_stds is not None and len(road_edge_stds) > 1:
      edge_std = float(road_edge_stds[0 if direction > 0 else 1])   # roadEdges[0]=左,[1]=右
      if edge_std > EDGE_STD_MAX:
        clearance = 0.0
    # C9:偏置侧预算折进本帧生效上限（bsm_same 的禁止语义 = 该侧预算 0,自然覆盖）。
    eff_offset = max_offset
    if direction > 0:
      eff_offset = min(eff_offset, budget_left)
    elif direction < 0:
      eff_offset = min(eff_offset, budget_right)
    y_des = plan(targets, max_offset=eff_offset)

    # Hysteresis tracks target presence, NOT the budget-capped response: with
    # presence keyed to the unbudgeted offset, a BSM flicker only zeroes the
    # bias for its duration instead of resetting the state machine (exit 1.0s
    # + re-enter 0.5s of dead window after every alert would be far worse).
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
        "maxOffset": eff_offset, "edgeClearance": clearance,
        "budgetLeft": budget_left, "budgetRight": budget_right, "vEgo": v_ego,
      }
      return float(model_curvature), False

    bias = self._bias.update(y_des)
    self.last_state = {
      "active": self._active, "direction": direction, "yDes": y_des, "bias": bias,
      "maxOffset": eff_offset, "edgeClearance": clearance,
      "budgetLeft": budget_left, "budgetRight": budget_right, "vEgo": v_ego,
    }
    return float(model_curvature) + 2.0 * bias / L_LOOKAHEAD ** 2, True
