"""Offline P0 shadow evaluation for avoidanced.

This harness replays the avoidance planner over a route log **without ever
publishing** ``lateralManeuverPlan``: it is the "record only, don't send" step
from the design doc §4. Run it with ``AvoidanceEnabled`` off to confirm the bias
lines up with the projection and does not move the car before P1.

What it records (per 5Hz frame, plus a summary):

* **association rate** — fraction of in-gate radar targets that a vision object
  corroborates. Two vision sides: the default route replay proxies with
  ``modelV2.leadsV3`` at t=0 (route logs carry no camera frames or YOLO boxes);
  with a detector injected (``ShadowEvaluator(detector=...)``) each frame runs
  the real daemon chain — YOLO boxes -> ground-plane projection -> association
  — on synthetic camera data (tests only). Target: ``ASSOC_RATE_MIN`` (> 0.80).
* **calibration alignment** — max lateral residual ``|radar.yRel - vision.y|``
  over associated pairs. Target: ``CALIB_MAX_RESIDUAL_M`` (< 0.30 m, spec §4).
* **false triggers** — valid frames with a non-zero bias whose target had no
  vision corroboration (a radar ghost); and the number of activation segments.
* **latency** — planner-only wall time per frame (the full-process latency needs
  the device).
* **jerk** — ``v_ego**2 * d(curvature)/dt``, the same quantity ``clip_curvature``
  bounds with ``MAX_LATERAL_JERK``.
* **execution closure** — per activation segment: nearest in-gate target yRel
  drift, mean ``yDes``, avoidance-side road-edge clearance change, and the
  displacement integrated from the executed curvature bias vs the raw commanded
  offset (``closure_ratio``). In replay this grades the plan's own execution
  (low-pass lag); on device the same metric over telemetry curvature is the
  full vehicle closure. See the ``execution_closure`` summary field.

The real P0 run is the avoidanced daemon on the device (real camera + YOLO pkl)
with metrics collected over lanlink/CSV; this replay tool grades offline planner
metrics on route logs.

Usage::

    python -m openpilot.selfdrive.avoidanced.shadow <route> --out /tmp/shadow
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.association import associate as associate_daemon
from openpilot.selfdrive.avoidanced.association import nearest_pairs_by_bearing
from openpilot.selfdrive.avoidanced.avoidance_planner import AvoidancePlanner, _in_gate, fuse_targets, radar_point_key
from openpilot.selfdrive.avoidanced.projection import RoiMeta, project_detections

if TYPE_CHECKING:
  import numpy as np

# Acceptance thresholds (spec §3/§4 and the Task 6 brief).
ASSOC_RATE_MIN = 0.80
CALIB_MAX_RESIDUAL_M = 0.30
MAX_LATERAL_JERK = 5.0          # m/s^3, mirrors drive_helpers.MAX_LATERAL_JERK
MAX_PROCESS_LATENCY_MS = 200.0  # 5Hz budget, informational off-device

# Offline association gates. The bearing gate is wider than the daemon's
# (0.035 rad) for the P0 statistics; ASSOC_MAX_DRANGE_SHADOW stays as the
# secondary range-difference check (same bearing but wildly different range
# must not pair). The old Cartesian dx gate is meaningless on a bearing matcher.
ASSOC_MAX_DBEARING_SHADOW = 0.06  # rad, ~3.4deg
ASSOC_MAX_DRANGE_SHADOW = 1.5     # m, secondary range-difference gate

# Vision side is only trusted above this model lead probability.
VISION_MIN_PROB = 0.5


@dataclass(frozen=True)
class RadarTarget:
  dRel: float
  yRel: float
  # m/s relative velocity; None means the source did not provide one. It must
  # stay None (NOT default to 0.0): fuse_targets treats a missing vRel as
  # static (needs vision confirmation), while 0.0 + v_ego reads as a mover.
  vRel: float | None = None


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
  road_edges: Sequence = ()
  # modelV2.meta.laneChangeState != off: the planner suppresses the bias during
  # lane changes (same gate as the daemon, Task 6).
  lane_change_active: bool = False
  # Camera side for the fused path. Synthetic frames only: route logs carry no
  # camera frames, so iter_frames never fills these.
  roi_frame: np.ndarray | None = None
  roi_meta: RoiMeta | None = None
  intrinsics: tuple[float, float, float, float] | None = None


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
  # Execution-closure observability (see summarize's ``execution_closure``).
  # Defaults keep record constructors that predate the closure metrics working;
  # ShadowEvaluator.step fills them every frame.
  v_ego: float = 0.0
  direction: int = 0                  # -1 right / 0 none / +1 left (planner decision)
  target_y: float | None = None       # yRel of the nearest in-gate target, None if none
  edge_clearance: float | None = None  # avoidance-side road-edge clearance, None if no edge
  y_des_cmd: float = 0.0              # raw (pre-low-pass) commanded offset from the planner


def associate(radar_points: Iterable[RadarTarget], vision_objects: Iterable[VisionObject],
              fy: float = 425.25,
              max_dbearing: float = ASSOC_MAX_DBEARING_SHADOW,
              max_drange: float = ASSOC_MAX_DRANGE_SHADOW) -> list[tuple[RadarTarget, VisionObject, float, str | None]]:
  """Radar<->vision association on bearing (the offline wide-gate variant).

  Thin wrapper over the shared bearing matcher in :mod:`association` (the daemon
  path uses the same matcher with a tighter bearing gate). Returns
  ``(radar, vision, dbearing, vision_cls)`` tuples, one per matched radar
  target; ``vision_cls`` is None when the vision side carries no class.
  """
  pairs, _ = nearest_pairs_by_bearing(radar_points, vision_objects, fy, max_dbearing, max_drange)
  return pairs


class ShadowEvaluator:
  """Runs the real planner over frames and records metrics; never publishes.

  ``detector=None`` keeps the ``modelV2.leadsV3`` proxy vision side (route
  replay). With a detector injected, frames carrying camera data
  (``roi_frame``/``roi_meta``/``intrinsics``) run the real fused path instead:
  detector -> ground-plane projection -> daemon association -> planner.
  """

  def __init__(self, planner: AvoidancePlanner | None = None, max_offset: float = C.MAX_OFFSET_FREE,
               clock=time.perf_counter, detector=None):
    self.planner = planner if planner is not None else AvoidancePlanner()
    self.max_offset = max_offset
    self._clock = clock
    self.detector = detector
    self.records: list[ShadowRecord] = []

  def _project(self, frame: ShadowFrame) -> list[dict] | None:
    """Detector -> car-frame projections; ``None`` keeps the leadsV3 proxy path."""
    if self.detector is None:
      return None
    if frame.roi_frame is None or frame.roi_meta is None or frame.intrinsics is None:
      raise ValueError("detector injected but the frame carries no camera data (roi_frame/roi_meta/intrinsics)")
    detections = self.detector.infer(frame.roi_frame, now=frame.t)
    fx, fy, cx, cy = frame.intrinsics
    return project_detections(detections, fx=fx, fy=fy, cx=cx, cy=cy,
                              height=C.CAMERA_HEIGHT, pitch=C.CAMERA_PITCH, yaw=C.CAMERA_YAW,
                              camera_to_front=C.CAMERA_TO_FRONT, roi_meta=frame.roi_meta)

  def step(self, frame: ShadowFrame) -> ShadowRecord:
    projected = self._project(frame)
    # fy for the association gates: the fused path guarantees intrinsics (see
    # _project); the proxy path never reads fy (VisionObject ranges come from
    # the object's own x), so 0.0 is a safe placeholder there.
    fy = frame.intrinsics[1] if frame.intrinsics is not None else 0.0
    if projected is not None:
      # Fused path (daemon parity): association decides which detections the
      # radar points absorb; the rest stay independent planner targets. The
      # pairs also feed fuse_targets: confirmed points survive the static-speed
      # gate and take the vision class weight, exactly like the daemon.
      _, fused, pairs = associate_daemon(frame.radar_points, projected, fy=fy)
      confirmed_keys = tuple(radar_point_key(p[0]) for p in pairs)
      vision_cls_by_key = {radar_point_key(p[0]): p[3] for p in pairs}
      targets = fuse_targets(frame.radar_points, fused, v_ego=frame.v_ego,
                             confirmed_keys=confirmed_keys,
                             vision_cls_by_key=vision_cls_by_key)
      vision_objects = [VisionObject(x=float(d["dRel"]), y=float(d["yRel"])) for d in projected]
      # Metrics radar side stays radar-only (in-gate), so a vision-only target
      # cannot pair with itself and inflate the association rate.
      metric_radar = [p for p in frame.radar_points if _in_gate(float(p.dRel), float(p.yRel))]
    else:
      # Proxy path has no vision confirmation available (leadsV3 is metrics
      # only), so it degrades like the daemon's radar-only fallback: static
      # radar points are dropped, movers still drive the plan.
      targets = fuse_targets(frame.radar_points, v_ego=frame.v_ego)
      vision_objects = frame.vision_objects
      metric_radar = targets

    t0 = self._clock()
    curvature, valid = self.planner.update(
      model_curvature=frame.model_curvature,
      targets=targets,
      v_ego=frame.v_ego,
      bsm_left=frame.bsm_left,
      bsm_right=frame.bsm_right,
      road_edges=frame.road_edges,
      lane_change_active=frame.lane_change_active,
      max_offset=self.max_offset,
      now=frame.t,
    )
    latency_ms = (self._clock() - t0) * 1e3

    pairs = associate(metric_radar, vision_objects, fy=fy)
    residual = max((abs(radar.yRel - obj.y) for radar, obj, _, _ in pairs), default=None)

    jerk = None
    if self.records:
      dt = frame.t - self.records[-1].t
      if dt > 0.0:
        jerk = abs(frame.v_ego ** 2 * (curvature - self.records[-1].curvature) / dt)

    # The planner returns model + bias, so invert bias_curv = 2*y_des/L^2.
    y_des = (curvature - frame.model_curvature) * C.L_LOOKAHEAD ** 2 / 2.0

    # Execution-closure observability: the planner's decision state (direction,
    # raw yDes, direction-aware edge clearance) plus the nearest in-gate target
    # the segment's yRel drift is tracked against.
    state = self.planner.last_state
    in_gate_targets = [tg for tg in targets if _in_gate(float(tg.dRel), float(tg.yRel))]
    nearest = min(in_gate_targets, key=lambda tg: float(tg.dRel)) if in_gate_targets else None
    clearance = float(state.get("edgeClearance", float("inf")))

    record = ShadowRecord(
      t=frame.t,
      model_curvature=frame.model_curvature,
      curvature=curvature,
      valid=valid,
      y_des=y_des,
      n_radar=len(metric_radar),
      n_vision=len(vision_objects),
      n_associated=len(pairs),
      lat_residual=residual,
      latency_ms=latency_ms,
      jerk=jerk,
      v_ego=float(frame.v_ego),
      direction=int(state.get("direction", 0)),
      target_y=float(nearest.yRel) if nearest is not None else None,
      edge_clearance=None if math.isinf(clearance) else clearance,
      y_des_cmd=float(state.get("yDes", 0.0)),
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


def _valid_segments(records: Sequence[ShadowRecord]) -> list[list[ShadowRecord]]:
  """Maximal runs of consecutive valid frames (the activation segments)."""
  segments: list[list[ShadowRecord]] = []
  current: list[ShadowRecord] = []
  for record in records:
    if record.valid:
      current.append(record)
    elif current:
      segments.append(current)
      current = []
  if current:
    segments.append(current)
  return segments


def _double_integral(times: Sequence[float], values: Sequence[float]) -> float:
  """Trapezoidal ∫∫ values dt dt (accel -> velocity -> displacement), zero initial velocity."""
  velocity = 0.0
  displacement = 0.0
  for i in range(1, len(times)):
    dt = times[i] - times[i - 1]
    velocity_next = velocity + 0.5 * (values[i - 1] + values[i]) * dt
    displacement += 0.5 * (velocity + velocity_next) * dt
    velocity = velocity_next
  return displacement


def _segment_closure(segment: Sequence[ShadowRecord]) -> dict:
  """Execution-closure stats for one activation segment.

  * target yRel drift: nearest in-gate target yRel, segment end minus start —
    the object the avoidance is pushing away from;
  * yDes mean: the executed (post-low-pass) offset command;
  * road-edge clearance change on the avoidance side;
  * closure ratio: displacement integrated from the executed curvature bias
    (``∫∫ v²·(curvature - model_curvature) dt²`` — post-low-pass, what the plan
    actually bends) vs the displacement the RAW pre-low-pass commanded offset
    implies (``∫∫ v²·2·y_des_cmd/L² dt²``). Both terms share the left-positive
    y convention; the ratio (executed / commanded) deliberately compares the
    executed plan against the raw command: in replay the gap is the low-pass
    lag (→ 1 for steady segments); on device the same metric over telemetry
    curvature is the full vehicle closure.
  """
  times = [r.t for r in segment]
  measured = _double_integral(times, [r.v_ego ** 2 * (r.curvature - r.model_curvature) for r in segment])
  commanded = _double_integral(times, [r.v_ego ** 2 * 2.0 * r.y_des_cmd / C.L_LOOKAHEAD ** 2 for r in segment])

  target_ys = [r.target_y for r in segment if r.target_y is not None]
  clearances = [r.edge_clearance for r in segment if r.edge_clearance is not None]
  y_des_values = [r.y_des for r in segment]
  y_des_cmd_values = [r.y_des_cmd for r in segment]

  return {
    "start_t": segment[0].t,
    "end_t": segment[-1].t,
    "frames": len(segment),
    "target_y_start_m": target_ys[0] if target_ys else None,
    "target_y_end_m": target_ys[-1] if target_ys else None,
    "target_y_change_m": (target_ys[-1] - target_ys[0]) if len(target_ys) >= 2 else None,
    "y_des_mean_m": sum(y_des_values) / len(y_des_values),
    "y_des_cmd_mean_m": sum(y_des_cmd_values) / len(y_des_cmd_values),
    "edge_clearance_start_m": clearances[0] if clearances else None,
    "edge_clearance_end_m": clearances[-1] if clearances else None,
    "edge_clearance_change_m": (clearances[-1] - clearances[0]) if len(clearances) >= 2 else None,
    "displacement_measured_m": measured,
    "displacement_commanded_m": commanded,
    "closure_ratio": measured / commanded if abs(commanded) > 1e-4 else None,
  }


def summarize(records: Sequence[ShadowRecord]) -> dict:
  """Aggregate records into the P0 acceptance metrics."""
  radar_total = sum(r.n_radar for r in records)
  associated = sum(r.n_associated for r in records)
  # NOTE(Task 7): the denominator is currently radar_total (every radar target).
  # The spec wants "vision detections within the radar FOV" instead; re-tier
  # ASSOC_RATE_MIN together with that change.
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

  closure_segments = [_segment_closure(segment) for segment in _valid_segments(records)]
  ratios = [s["closure_ratio"] for s in closure_segments if s["closure_ratio"] is not None]
  execution_closure = {
    "segments": closure_segments,
    "mean_closure_ratio": sum(ratios) / len(ratios) if ratios else None,
  }

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
    "execution_closure": execution_closure,
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
  the test doubles both satisfy this). Route logs carry no camera frames, so
  these frames always take the leadsV3 proxy vision side.
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
      radar_points=[RadarTarget(dRel=float(p.dRel), yRel=float(p.yRel),
                                vRel=getattr(p, "vRel", None)) for p in radar.points],
      vision_objects=_vision_from_model(model),
      bsm_left=bool(getattr(car, "leftBlindspot", False)),
      bsm_right=bool(getattr(car, "rightBlindspot", False)),
      road_edges=getattr(model, "roadEdges", []) or [],
      lane_change_active=str(getattr(getattr(model, "meta", None), "laneChangeState", "off")) != "off",
    )
    emitted += 1
    if limit is not None and emitted >= limit:
      return


def evaluate_records(frames: Iterable[ShadowFrame], planner: AvoidancePlanner | None = None,
                     max_offset: float = C.MAX_OFFSET_FREE, clock=time.perf_counter,
                     detector=None) -> tuple[list[ShadowRecord], dict]:
  evaluator = ShadowEvaluator(planner=planner, max_offset=max_offset, clock=clock, detector=detector)
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
  """Replay a route log in shadow mode and (optionally) write the report.

  Route logs carry no camera frames, so this always runs the leadsV3 proxy
  vision side; the fused path needs the on-device daemon (or a detector
  injected over synthetic frames in tests).
  """
  from openpilot.tools.lib.logreader import LogReader

  frames = iter_frames(LogReader(route), sample_period=sample_period, limit=limit)
  records, summary = evaluate_records(frames, max_offset=max_offset)
  if out_dir is not None:
    write_report(records, summary, out_dir)
  return summary


def _fmt(value: float | None, spec: str = "{:+.3f}") -> str:
  return "n/a" if value is None else spec.format(value)


def print_execution_closure(summary: dict) -> None:
  """Human-readable per-segment execution-closure block for the terminal."""
  closure = summary.get("execution_closure") or {}
  segments = closure.get("segments") or []
  if not segments:
    print("execution closure: no activation segments")
    return
  mean_ratio = closure.get("mean_closure_ratio")
  print(f"execution closure: {len(segments)} segment(s), mean closure ratio {_fmt(mean_ratio)}")
  for seg in segments:
    line = (f"  t {seg['start_t']:.1f}-{seg['end_t']:.1f}s frames={seg['frames']}"
            + f" | target yRel {_fmt(seg['target_y_start_m'])} -> {_fmt(seg['target_y_end_m'])} (d {_fmt(seg['target_y_change_m'])})"
            + f" | yDes mean {_fmt(seg['y_des_mean_m'])} m"
            + f" | edge clearance {_fmt(seg['edge_clearance_start_m'])} -> {_fmt(seg['edge_clearance_end_m'])} (d {_fmt(seg['edge_clearance_change_m'])})"
            + f" | displacement meas/cmd {_fmt(seg['displacement_measured_m'])}/{_fmt(seg['displacement_commanded_m'])} m"
            + f" | closure ratio {_fmt(seg['closure_ratio'])}")
    print(line)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Offline P0 shadow evaluation for avoidanced (records, never publishes).")
  parser.add_argument("route", help="route or local log path accepted by LogReader")
  parser.add_argument("--out", default=None, help="directory for shadow.csv / shadow_summary.json")
  parser.add_argument("--max-offset", type=float, default=C.MAX_OFFSET_FREE, help="bias cap in metres")
  parser.add_argument("--limit", type=int, default=None, help="stop after N frames")
  args = parser.parse_args(argv)

  summary = evaluate_log(args.route, out_dir=args.out, max_offset=args.max_offset, limit=args.limit)
  print(json.dumps(summary, indent=2))
  print_execution_closure(summary)
  return 0 if summary["pass"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
