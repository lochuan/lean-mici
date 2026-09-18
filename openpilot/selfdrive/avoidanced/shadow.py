"""Offline P0 shadow evaluation for avoidanced.

This harness replays the avoidance planner over a route log **without ever
publishing** ``lateralManeuverPlan``: it is the "record only, don't send" step
from the design doc §4. Run it with ``AvoidanceEnabled`` off to confirm the bias
lines up with the projection and does not move the car before P1.

What it records (per 5Hz frame, plus a summary):

* **association rate** — fraction of in-gate radar targets that a vision object
  corroborates. Until the camera -> YOLO -> projection/association chain lands
  (Task 5 I3, unowned), the vision side is ``modelV2.leadsV3`` at t=0 used as a
  stand-in; :func:`associate` is generic and will take projected YOLO boxes once
  that task exists. Target: ``ASSOC_RATE_MIN`` (> 0.80).
* **calibration alignment** — max lateral residual ``|radar.yRel - vision.y|``
  over associated pairs. Target: ``CALIB_MAX_RESIDUAL_M`` (< 0.30 m, spec §4).
* **false triggers** — valid frames with a non-zero bias whose target had no
  vision corroboration (a radar ghost); and the number of activation segments.
* **latency** — planner-only wall time per frame (the full-process latency needs
  the device).
* **jerk** — ``v_ego**2 * d(curvature)/dt``, the same quantity ``clip_curvature``
  bounds with ``MAX_LATERAL_JERK``.

Usage::

    python -m openpilot.selfdrive.avoidanced.shadow <route> --out /tmp/shadow
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.avoidance_planner import AvoidancePlanner, edge_clearance, fuse_targets

# Acceptance thresholds (spec §3/§4 and the Task 6 brief).
ASSOC_RATE_MIN = 0.80
CALIB_MAX_RESIDUAL_M = 0.30
MAX_LATERAL_JERK = 5.0          # m/s^3, mirrors drive_helpers.MAX_LATERAL_JERK
MAX_PROCESS_LATENCY_MS = 200.0  # 5Hz budget, informational off-device

# Radar <-> vision association gates in the car frame (metres).
ASSOC_MAX_DX = 3.0
ASSOC_MAX_DY = 1.5

# Vision side is only trusted above this model lead probability.
VISION_MIN_PROB = 0.5


@dataclass(frozen=True)
class RadarTarget:
  dRel: float
  yRel: float


@dataclass(frozen=True)
class VisionObject:
  x: float
  y: float


@dataclass(frozen=True)
class ShadowFrame:
  t: float
  model_curvature: float
  v_ego: float
  radar_points: Sequence[RadarTarget] = ()
  vision_objects: Sequence[VisionObject] = ()
  bsm_left: bool = False
  bsm_right: bool = False
  clearance: float = float("inf")


@dataclass
class ShadowRecord:
  t: float
  model_curvature: float
  curvature: float
  valid: bool
  y_des: float
  n_radar: int
  n_vision: int
  n_associated: int
  lat_residual: float | None
  latency_ms: float
  jerk: float | None


def associate(radar_points: Iterable[RadarTarget], vision_objects: Iterable[VisionObject],
              max_dx: float = ASSOC_MAX_DX, max_dy: float = ASSOC_MAX_DY) -> list[tuple[RadarTarget, VisionObject, float, float]]:
  """Nearest-neighbour radar<->vision association within the dx/dy gates.

  Returns ``(radar, vision, dx, dy)`` tuples, one per matched radar target.
  """
  vision = tuple(vision_objects)
  pairs: list[tuple[RadarTarget, VisionObject, float, float]] = []
  for radar in radar_points:
    best: tuple[float, VisionObject, float, float] | None = None
    for obj in vision:
      dx, dy = abs(radar.dRel - obj.x), abs(radar.yRel - obj.y)
      if dx <= max_dx and dy <= max_dy:
        dist = dx * dx + dy * dy
        if best is None or dist < best[0]:
          best = (dist, obj, dx, dy)
    if best is not None:
      pairs.append((radar, best[1], best[2], best[3]))
  return pairs


class ShadowEvaluator:
  """Runs the real planner over frames and records metrics; never publishes."""

  def __init__(self, planner: AvoidancePlanner | None = None, max_offset: float = C.MAX_OFFSET_FREE,
               clock=time.perf_counter):
    self.planner = planner if planner is not None else AvoidancePlanner()
    self.max_offset = max_offset
    self._clock = clock
    self.records: list[ShadowRecord] = []

  def step(self, frame: ShadowFrame) -> ShadowRecord:
    targets = fuse_targets(frame.radar_points)

    t0 = self._clock()
    curvature, valid = self.planner.update(
      model_curvature=frame.model_curvature,
      targets=targets,
      v_ego=frame.v_ego,
      bsm_left=frame.bsm_left,
      bsm_right=frame.bsm_right,
      clearance=frame.clearance,
      max_offset=self.max_offset,
      now=frame.t,
    )
    latency_ms = (self._clock() - t0) * 1e3

    pairs = associate(targets, frame.vision_objects)
    residual = max((abs(radar.yRel - obj.y) for radar, obj, _, _ in pairs), default=None)

    jerk = None
    if self.records:
      dt = frame.t - self.records[-1].t
      if dt > 0.0:
        jerk = abs(frame.v_ego ** 2 * (curvature - self.records[-1].curvature) / dt)

    # The planner returns model + bias, so invert bias_curv = 2*y_des/L^2.
    y_des = (curvature - frame.model_curvature) * C.L_LOOKAHEAD ** 2 / 2.0

    record = ShadowRecord(
      t=frame.t,
      model_curvature=frame.model_curvature,
      curvature=curvature,
      valid=valid,
      y_des=y_des,
      n_radar=len(targets),
      n_vision=len(frame.vision_objects),
      n_associated=len(pairs),
      lat_residual=residual,
      latency_ms=latency_ms,
      jerk=jerk,
    )
    self.records.append(record)
    return record

  def run(self, frames: Iterable[ShadowFrame]) -> list[ShadowRecord]:
    for frame in frames:
      self.step(frame)
    return self.records


def _percentile(values: list[float], pct: float) -> float:
  if not values:
    return 0.0
  ordered = sorted(values)
  idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
  return ordered[idx]


def summarize(records: Sequence[ShadowRecord]) -> dict:
  """Aggregate records into the P0 acceptance metrics."""
  radar_total = sum(r.n_radar for r in records)
  associated = sum(r.n_associated for r in records)
  association_rate = associated / radar_total if radar_total else None

  residuals = [r.lat_residual for r in records if r.lat_residual is not None]
  calib_max = max(residuals) if residuals else None

  jerks = [r.jerk for r in records if r.jerk is not None]
  max_jerk = max(jerks) if jerks else None

  latencies = [r.latency_ms for r in records]
  valid_frames = sum(1 for r in records if r.valid)
  bias_frames = [r for r in records if r.valid and abs(r.y_des) > 1e-9]
  false_triggers = sum(1 for r in bias_frames if r.n_associated == 0)

  activation_segments = 0
  prev_valid = False
  for r in records:
    if r.valid and not prev_valid:
      activation_segments += 1
    prev_valid = r.valid

  p95_latency = _percentile(latencies, 95.0)
  max_latency = max(latencies) if latencies else 0.0

  checks = {
    "association_rate": None if association_rate is None else association_rate >= ASSOC_RATE_MIN,
    "calibration": None if calib_max is None else calib_max <= CALIB_MAX_RESIDUAL_M,
    "jerk": None if max_jerk is None else max_jerk <= MAX_LATERAL_JERK,
    "latency": p95_latency <= MAX_PROCESS_LATENCY_MS,
  }

  return {
    "frames": len(records),
    "radar_targets": radar_total,
    "associated_targets": associated,
    "association_rate": association_rate,
    "calibration_max_residual_m": calib_max,
    "max_lateral_jerk": max_jerk,
    "p95_latency_ms": p95_latency,
    "max_latency_ms": max_latency,
    "valid_frames": valid_frames,
    "bias_frames": len(bias_frames),
    "false_trigger_frames": false_triggers,
    "activation_segments": activation_segments,
    "insufficient_data": radar_total == 0,
    "checks": checks,
    "pass": all(v is not False for v in checks.values()),
  }


def _vision_from_model(model) -> list[VisionObject]:
  leads = getattr(model, "leadsV3", None) or []
  objects: list[VisionObject] = []
  for lead in leads:
    if float(getattr(lead, "prob", 0.0)) < VISION_MIN_PROB:
      continue
    xs, ys = getattr(lead, "x", None), getattr(lead, "y", None)
    if not xs or not ys:
      continue
    objects.append(VisionObject(x=float(xs[0]), y=float(ys[0])))
  return objects


def iter_frames(messages: Iterable, sample_period: float = 0.2, limit: int | None = None) -> Iterator[ShadowFrame]:
  """Turn a message stream into 5Hz shadow frames (latest model/car/radar state).

  ``messages`` items need ``.which()`` and ``.logMonoTime`` (cereal readers and
  the test doubles both satisfy this).
  """
  model = car = radar = None
  last_emit: float | None = None
  emitted = 0
  for msg in messages:
    which = msg.which()
    if which == "modelV2":
      model = msg.modelV2
    elif which == "carState":
      car = msg.carState
    elif which == "radarTracks":
      radar = msg.radarTracks
    else:
      continue

    if which != "radarTracks" or model is None or car is None or radar is None:
      continue

    t = msg.logMonoTime / 1e9
    if last_emit is not None and (t - last_emit) < sample_period:
      continue
    last_emit = t
    yield ShadowFrame(
      t=t,
      model_curvature=float(model.action.desiredCurvature),
      v_ego=float(car.vEgo),
      radar_points=[RadarTarget(dRel=float(p.dRel), yRel=float(p.yRel)) for p in radar.points],
      vision_objects=_vision_from_model(model),
      bsm_left=bool(getattr(car, "leftBlindspot", False)),
      bsm_right=bool(getattr(car, "rightBlindspot", False)),
      clearance=edge_clearance(getattr(model, "roadEdges", []) or []),
    )
    emitted += 1
    if limit is not None and emitted >= limit:
      return


def evaluate_records(frames: Iterable[ShadowFrame], planner: AvoidancePlanner | None = None,
                     max_offset: float = C.MAX_OFFSET_FREE, clock=time.perf_counter) -> tuple[list[ShadowRecord], dict]:
  evaluator = ShadowEvaluator(planner=planner, max_offset=max_offset, clock=clock)
  records = evaluator.run(frames)
  return records, summarize(records)


def write_report(records: Sequence[ShadowRecord], summary: dict, out_dir: str | Path) -> tuple[Path, Path]:
  out = Path(out_dir)
  out.mkdir(parents=True, exist_ok=True)
  csv_path = out / "shadow.csv"
  json_path = out / "shadow_summary.json"

  fields = list(ShadowRecord.__dataclass_fields__.keys())
  with csv_path.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    for record in records:
      writer.writerow(asdict(record))

  json_path.write_text(json.dumps(summary, indent=2) + "\n")
  return csv_path, json_path


def evaluate_log(route: str, out_dir: str | Path | None = None, max_offset: float = C.MAX_OFFSET_FREE,
                 sample_period: float = 0.2, limit: int | None = None) -> dict:
  """Replay a route log in shadow mode and (optionally) write the report."""
  from openpilot.tools.lib.logreader import LogReader

  frames = iter_frames(LogReader(route), sample_period=sample_period, limit=limit)
  records, summary = evaluate_records(frames, max_offset=max_offset)
  if out_dir is not None:
    write_report(records, summary, out_dir)
  return summary


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Offline P0 shadow evaluation for avoidanced (records, never publishes).")
  parser.add_argument("route", help="route or local log path accepted by LogReader")
  parser.add_argument("--out", default=None, help="directory for shadow.csv / shadow_summary.json")
  parser.add_argument("--max-offset", type=float, default=C.MAX_OFFSET_FREE, help="bias cap in metres")
  parser.add_argument("--limit", type=int, default=None, help="stop after N frames")
  args = parser.parse_args(argv)

  summary = evaluate_log(args.route, out_dir=args.out, max_offset=args.max_offset, limit=args.limit)
  print(json.dumps(summary, indent=2))
  return 0 if summary["pass"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
