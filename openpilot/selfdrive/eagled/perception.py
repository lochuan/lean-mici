"""eagled perception core: the fusion chain behind the eagle.

Turns raw sensors into the per-frame target picture: wide-road camera frame
-> YOLO ROI inference -> ground-plane projection into the car frame -> radar
association -> ``fuse_targets``. No cereal, no params, no decisions here —
``EagleDaemon`` drives :class:`PerceptionCore` once per 5Hz tick and
publishes what it produces (``eagleState`` for consumers, ``eagleDebug``
for the lanlink UI and calibration); the avoidance planner is simply the
first in-process consumer of :class:`PerceptionFrame`. The planned second
consumer (desire_helper in modeld, lane-change gating) reads the published
``eagleState`` instead.

Module taxonomy: fusion primitives (``RadarPoint``/``Target``/``_sign``/
``_in_gate``/``radar_point_key``/``fuse_targets``) live HERE.
``avoidance_planner`` re-exports them for import compatibility and keeps the
decision layer (``plan``/``AvoidancePlanner``) on top of them.

When the camera stream, calibration or the YOLO weights are unavailable the
core degrades to radar-only: the reason is logged once and the frame keeps
publishing with radar-fused targets only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openpilot.common.swaglog import cloudlog

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.association import associate
from openpilot.selfdrive.eagled.camera_stream import CameraStream
from openpilot.selfdrive.eagled.projection import geometry_from_calibration, horizon_row_for, project_detections
from openpilot.selfdrive.eagled.yolo_detector import YoloDetector

YOLO_PKL_PATH = Path(__file__).parent / "models" / "yolo_tinygrad.pkl"


class RadarPoint(Protocol):
  dRel: float
  yRel: float
  vRel: float


@dataclass(frozen=True)
class Target:
  side: int    # -1 = right of ego (yRel < 0), +1 = left
  dRel: float  # m, longitudinal distance
  yRel: float  # m, lateral position, left positive
  w: float     # class weight (VRU > vehicle)
  conf: float  # detector confidence [0, 1]


def _sign(value: float) -> int:
  return 1 if value >= 0.0 else -1


def _in_gate(dRel: float, yRel: float) -> bool:
  return 0.0 < dRel <= C.D_GATE and C.OWN_LANE_HALF_WIDTH <= abs(yRel) <= C.Y_GATE


def radar_point_key(point) -> int:
  """Stable identity key for a radar point across re-iteration.

  pycapnp constructs a fresh wrapper object on every access to
  ``radarTracks.points``, so ``id()`` differs between the pass that built the
  association pairs and any later pass over the same message -- on device an
  ``id()`` key never matches. ``trackId`` is the identity-stable key (required
  UInt64, no reuse, per car.capnp); ``id()`` remains only as the fallback for
  duck-typed fixtures that carry no ``trackId``. All confirmation-key building
  (fuse_targets and every call site) goes through this helper.
  """
  track_id = getattr(point, "trackId", None)
  return id(point) if track_id is None else int(track_id)


def fuse_targets(radar_points: Iterable[RadarPoint], detections: Iterable[dict] | None = None,
                 v_ego: float = 0.0, confirmed_keys: Iterable[int] = (),
                 vision_cls_by_key: dict[int, str] | None = None) -> list[Target]:
  """Build planner targets from radar points plus unmatched vision detections.

  ``confirmed_keys`` holds the :func:`radar_point_key` of the radar points that
  a vision detection confirmed, and ``vision_cls_by_key`` maps those keys to the
  vision class. Keys are ``trackId`` (stable across capnp re-iteration),
  never ``id()`` -- see :func:`radar_point_key`. Two things depend on them:

  * A radar point whose ground speed is near zero is kept ONLY when vision
    confirms it. Guardrails and bridge pillars sit at zero ground speed -- but
    so does a broken-down car, so a plain speed gate would discard a real
    hazard. Vision separates them: it reports a stopped car as ``car`` and does
    not report a guardrail as ``car``/``person``.
  * A confirmed radar point takes the vision class weight. Otherwise a
    radar-visible motorcycle is weighted 0.6 while one the radar missed is
    weighted 1.0 -- the better-perceived target counting for less.
  """
  confirmed = set(confirmed_keys)
  cls_by_key = vision_cls_by_key or {}
  targets: list[Target] = []
  for point in radar_points:
    dRel, yRel = float(point.dRel), float(point.yRel)
    if not _in_gate(dRel, yRel):
      continue
    key = radar_point_key(point)
    # vRel 缺失时保守按静止处理
    v_rel = getattr(point, "vRel", None)
    ground_speed = None if v_rel is None else abs(float(v_rel) + v_ego)
    if (ground_speed is None or ground_speed < C.STATIC_SPEED_THRESH) and key not in confirmed:
      continue
    cls = cls_by_key.get(key)
    weight = C.class_weight(cls)
    targets.append(Target(side=_sign(yRel), dRel=dRel, yRel=yRel, w=weight, conf=1.0))
  for det in detections or []:
    try:
      dRel, yRel = float(det["dRel"]), float(det["yRel"])
    except (KeyError, TypeError, ValueError):
      continue
    if not _in_gate(dRel, yRel):
      continue
    weight = C.class_weight(det.get("cls"))
    targets.append(Target(side=_sign(yRel), dRel=dRel, yRel=yRel, w=weight,
                          conf=float(det.get("conf", 1.0))))
  return targets


@dataclass
class PerceptionFrame:
  """One 5Hz fusion output: everything the publishers and the planner need."""
  radar_points: list          # materialized list of this tick's radar points
  detections: list[dict]      # car-frame projected YOLO detections
  n_associated: int
  pairs: list                 # (radar_point, det, pair_id, cls) from associate()
  confirmed_keys: tuple
  vision_cls_by_key: dict
  targets: list[Target]       # planner-ready fused targets


class PerceptionCore:
  """Stateful fusion chain: lazy camera/YOLO lifecycle + radar-only degrade."""

  def __init__(self, camera=None, detector=None, camera_factory=CameraStream):
    self.camera = camera                  # lazy: created via camera_factory on first use
    self.camera_factory = camera_factory
    self.detector = detector              # lazy: YoloDetector on first use
    self.detector_dead = False            # YOLO failed hard -> stop retrying
    self.degraded: set[str] = set()       # radar-only fallback reasons, logged once each

  def _degrade(self, reason: str) -> None:
    """Log a radar-only fallback reason once (never spam)."""
    if reason not in self.degraded:
      self.degraded.add(reason)
      cloudlog.warning(f"eagled: {reason} unavailable, radar-only fallback")

  def detect(self, extrinsics_msg, extrinsics_valid: bool, now: float) -> list[dict]:
    """Camera -> YOLO -> car-frame projections; ``[]`` keeps the frame radar-only."""
    geom = geometry_from_calibration(extrinsics_msg, extrinsics_valid)
    if not geom.valid:
      # 0.5deg pitch error = 41% distance error at 40m. Running the vision path
      # on an uncalibrated camera is exactly how spurious biases get produced,
      # so fall back to radar-only until openpilot's calibration converges.
      self._degrade("calibration")
      return []
    if self.camera is None:
      self.camera = self.camera_factory()
    # Intrinsics are only known after the first successful connect; until then
    # the ROI falls back to the frame centre (horizon_row=None).
    intrinsics = self.camera.intrinsics
    horizon_row = horizon_row_for(intrinsics[3], intrinsics[1], geom) if intrinsics is not None else None
    frame = self.camera.frame(horizon_row=horizon_row)
    if frame is None:
      # No camerad stream (PC) or no fresh frame this tick; connect keeps
      # retrying inside CameraStream, the reason is only logged once.
      self._degrade("camera")
      return []
    roi, roi_meta = frame
    if self.detector is None:
      self.detector = YoloDetector(YOLO_PKL_PATH)
    if self.detector_dead:
      return []
    try:
      detections = self.detector.infer(roi, now=now)
    except Exception:
      # Missing pkl (weights are not in the repo) or a hard inference failure:
      # radar-only from here on, logged once, never crash the daemon.
      self.detector_dead = True
      self._degrade("yolo")
      cloudlog.exception("eagled: YOLO inference failed")
      return []
    fx, fy, cx, cy = self.camera.intrinsics
    return project_detections(detections, fx=fx, fy=fy, cx=cx, cy=cy,
                              height=C.CAMERA_HEIGHT, pitch=geom.pitch,
                              yaw=geom.yaw, roll=geom.roll,
                              camera_to_front=C.CAMERA_TO_FRONT, roi_meta=roi_meta,
                              frame_height=self.camera.frame_size[1] if self.camera.frame_size else None)

  def process(self, sm, now: float, v_ego: float) -> PerceptionFrame:
    """Run the full fusion chain once and return the frame for this tick."""
    radar = sm['radarTracks']
    detections = self.detect(sm['extrinsicsCalibration'], sm.valid['extrinsicsCalibration'], now)
    # associate needs fy for the box-height fallback on unmatched detections.
    # Intrinsics live on the camera (None until the first successful connect),
    # and with no detections associate never reads fy, so 0.0 is a safe fallback.
    fy = self.camera.intrinsics[1] if self.camera is not None and self.camera.intrinsics else 0.0
    n_associated, fused, pairs = associate(radar.points, detections, fy=fy)
    # Confirmation keys are trackId-based (radar_point_key): pycapnp hands out a
    # fresh wrapper object on every access to radar.points, so id() keys built
    # from associate's materialized points would never match the points
    # fuse_targets iterates here.
    confirmed_keys = tuple(radar_point_key(p[0]) for p in pairs)
    vision_cls_by_key = {radar_point_key(p[0]): p[3] for p in pairs}
    targets = fuse_targets(radar.points, fused, v_ego=v_ego,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key)
    return PerceptionFrame(radar_points=list(radar.points), detections=detections,
                           n_associated=n_associated, pairs=pairs,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key, targets=targets)
