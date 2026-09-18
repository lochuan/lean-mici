"""Radar <-> vision nearest-neighbour association in the car frame.

The matching core is shared with the P0 shadow harness (:mod:`shadow`), which
keeps its own wider gates for the offline metrics; the daemon path gates at
dx 2 m / dy 1 m. A detection that matches a radar point is absorbed by it — the
radar point represents the object (no duplicate target), so only unmatched
detections, which the radar missed (typical for VRUs), come back as independent
targets for ``fuse_targets``.
"""

from __future__ import annotations

from collections.abc import Iterable

ASSOC_MAX_DX = 2.0  # m, longitudinal gate
ASSOC_MAX_DY = 1.0  # m, lateral gate


def _point_xy(point) -> tuple[float, float]:
  """Radar-side position: ``dRel``/``yRel`` attributes or dict keys."""
  if isinstance(point, dict):
    return float(point["dRel"]), float(point["yRel"])
  return float(point.dRel), float(point.yRel)


def _object_xy(obj) -> tuple[float, float]:
  """Vision-side position: projected detection dict or ``x``/``y`` attributes."""
  if isinstance(obj, dict):
    return float(obj["dRel"]), float(obj["yRel"])
  return float(obj.x), float(obj.y)


def nearest_pairs(radar_points: Iterable, vision_objects: Iterable,
                  max_dx: float, max_dy: float) -> tuple[list[tuple], set[int]]:
  """Nearest-neighbour matching within the dx/dy gates.

  Returns ``(pairs, matched)``: ``pairs`` holds one ``(radar, vision, dx, dy)``
  tuple per matched radar point (nearest by dx²+dy²), ``matched`` is the set of
  matched vision indices.
  """
  vision = list(vision_objects)
  pairs: list[tuple] = []
  matched: set[int] = set()
  for radar in radar_points:
    rx, ry = _point_xy(radar)
    best: tuple[float, int, float, float] | None = None
    for idx, obj in enumerate(vision):
      ox, oy = _object_xy(obj)
      dx, dy = abs(rx - ox), abs(ry - oy)
      if dx <= max_dx and dy <= max_dy:
        dist = dx * dx + dy * dy
        if best is None or dist < best[0]:
          best = (dist, idx, dx, dy)
    if best is not None:
      _, idx, dx, dy = best
      pairs.append((radar, vision[idx], dx, dy))
      matched.add(idx)
  return pairs, matched


def associate(radar_points: Iterable, detections: Iterable,
              max_dx: float = ASSOC_MAX_DX, max_dy: float = ASSOC_MAX_DY) -> tuple[int, list[dict], list[tuple]]:
  """Daemon-facing association: ``(n_associated, fused, pairs)``.

  ``fused`` holds the fused detections for ``fuse_targets``: matched detections
  are absorbed by their radar point (the radar range is the more reliable
  longitudinal position); only unmatched detections — objects the radar missed —
  are returned as independent targets. ``pairs`` holds one
  ``(radar, vision, pair_id)`` tuple per match, ``pair_id`` counting up from 1
  in match order (both sides of a pair share it; 0 means unpaired).

  Note the matched radar points keep the vehicle weight; upgrading them with the
  detection's class is a P0 refinement once the calibration is trusted.
  """
  detections = list(detections)
  matches, matched = nearest_pairs(radar_points, detections, max_dx, max_dy)
  fused = [det for idx, det in enumerate(detections) if idx not in matched]
  pairs = [(radar, obj, pid) for pid, (radar, obj, _, _) in enumerate(matches, start=1)]
  return len(matches), fused, pairs
