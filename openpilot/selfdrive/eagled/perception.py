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

Module taxonomy: fusion primitives (``RadarPoint``/``Target``/``_sign``/``_in_gate``/
``radar_point_key``/``fuse_targets``/``lane_geometry``/``gate_target``) live
HERE. ``avoidance_planner`` re-exports them for import compatibility and keeps
the decision layer (``plan``/``AvoidancePlanner``) on top of them.

When the camera stream, calibration or the YOLO weights are unavailable the
core degrades to radar-only: the reason is logged once and the frame keeps
publishing with radar-fused targets only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

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
  lane: int = 0     # -1 左邻 / 0 本道或重叠 / +1 右邻（分类未参与时 0）
  in_gate: bool = True  # fuse_targets 已按三级门控裁决；planner 不再重算几何门


@dataclass(frozen=True)
class LaneGeometry:
  """Ego-lane boundaries + model path in the modelV2 frame (y right-positive).

  ``left_valid``/``right_valid`` 是 C7 置信门（laneLineProbs/Stds）的逐边裁决：
  边界线磨损/无划线的那一侧为 False，该侧目标回退到 path-relative 层。
  """
  left_valid: bool
  right_valid: bool
  left_x: tuple
  left_y: tuple
  right_x: tuple
  right_y: tuple
  path_x: tuple
  path_y: tuple
  path_std: tuple


def lane_geometry(model_v2) -> LaneGeometry | None:
  """Extract ego-lane boundaries (laneLines[1]/[2]) + path with C7 quality flags.

  None = 模型几何整体不可用（字段缺失/列表为空）——目标判定回退固定带。
  对 duck-typed 测试桩安全：缺属性的假 modelV2 直接得 None。
  """
  lines = getattr(model_v2, "laneLines", None)
  probs = getattr(model_v2, "laneLineProbs", None)
  stds = getattr(model_v2, "laneLineStds", None)
  position = getattr(model_v2, "position", None)
  if lines is None or probs is None or stds is None or position is None:
    return None
  if len(lines) <= C.LANE_IDX_RIGHT or len(probs) <= C.LANE_IDX_RIGHT or len(stds) <= C.LANE_IDX_RIGHT:
    return None
  left, right = lines[C.LANE_IDX_LEFT], lines[C.LANE_IDX_RIGHT]
  if len(left.x) == 0 or len(right.x) == 0:
    return None
  left_valid = float(probs[C.LANE_IDX_LEFT]) >= C.LANE_PROB_MIN and float(stds[C.LANE_IDX_LEFT]) <= C.LANE_STD_MAX
  right_valid = float(probs[C.LANE_IDX_RIGHT]) >= C.LANE_PROB_MIN and float(stds[C.LANE_IDX_RIGHT]) <= C.LANE_STD_MAX
  path_x = tuple(position.x)
  # duck-typed position 桩可能没有 yStd -> tier 2 安全关闭
  path_std = tuple(getattr(position, "yStd", ()) or ())
  return LaneGeometry(
    left_valid=left_valid, right_valid=right_valid,
    left_x=tuple(left.x), left_y=tuple(left.y),
    right_x=tuple(right.x), right_y=tuple(right.y),
    path_x=path_x, path_y=tuple(position.y),
    path_std=path_std if len(path_std) == len(path_x) else (),
  )


def gate_target(dRel: float, yRel: float, cls, geo: LaneGeometry | None) -> tuple[bool, int]:
  """Threat decision + lane label for one candidate. 单一事实来源:
  ``fuse_targets``（planner 输入）和 ``_target_rows``（遥测）都走这里,
  遥测与行动不可能不一致。

  三级判定（C2 车道相对 + C7 置信门）:
  1. **lane-relative** — 本道该侧边界线可信（probs/stds 过门）: 车身边缘
     （中心 ± 类别半宽）越过车道线 = 威胁。"车屁股侵入车道"的语义来源。
  2. **path-relative** — 边界不可信、但 position.yStd 在目标距离处达标:
     相对模型路径的固定带（弯道上仍正确——同车道目标 ≈ 0 偏差被排除）。
  3. **fixed band** — 模型几何全不可信: 相对车体的固定 yRel 带（旧行为）。

  tier 1/2/3 都要求中心已偏出 ``OWN_LANE_HALF_WIDTH``: 0.35m 偏置绕不开
  本道中心的障碍,那留给纵向和驾驶员。

  Frame: 雷达 yRel 左正,modelV2 y 右正,本函数内部换算 ``y_m = -yRel``。
  """
  if not (0.0 < dRel <= C.D_GATE):
    return False, 0
  y_m = -yRel  # 雷达左正 -> modelV2 右正
  if geo is not None:
    if yRel > 0.0 and geo.left_valid:
      # 目标在左:车身边缘(y_m + hw)越过左边界即侵入本道
      bound = float(np.interp(dRel, geo.left_x, geo.left_y))
      intrudes = (y_m + C.class_half_width(cls)) > bound
      lane = -1 if y_m < bound else 0
      return intrudes and abs(yRel) >= C.OWN_LANE_HALF_WIDTH, lane
    if yRel < 0.0 and geo.right_valid:
      bound = float(np.interp(dRel, geo.right_x, geo.right_y))
      intrudes = (y_m - C.class_half_width(cls)) < bound
      lane = 1 if y_m > bound else 0
      return intrudes and abs(yRel) >= C.OWN_LANE_HALF_WIDTH, lane
    # tier 2: path-relative（边界该侧不可信时的弯道正确回退）
    if len(geo.path_x) > 0 and len(geo.path_std) == len(geo.path_x):
      if float(np.interp(dRel, geo.path_x, geo.path_std)) <= C.PATH_STD_MAX:
        # 路径横向偏差,转回左正 yRel 语义后套固定带
        d_y_rel = float(np.interp(dRel, geo.path_x, geo.path_y)) - y_m
        off_path = C.OWN_LANE_HALF_WIDTH <= abs(d_y_rel) <= C.Y_GATE
        lane = (-1 if d_y_rel > 0 else 1) if abs(d_y_rel) >= C.OWN_LANE_HALF_WIDTH else 0
        return off_path, lane
  # tier 3: fixed band（旧行为,模型几何全不可信时的兜底）
  in_band = C.OWN_LANE_HALF_WIDTH <= abs(yRel) <= C.Y_GATE
  return in_band, (-1 if yRel > 0 else 1) if in_band else 0


def _sign(value: float) -> int:
  return 1 if value >= 0.0 else -1


def _in_gate(dRel: float, yRel: float) -> bool:
  """Historical fixed-band predicate — gate_target 的 tier 3 语义定义。

  生产路径不再单独调用它（tier 3 内联在 gate_target 里,带回退的 lane 标签）,
  但测试用它构造带内/带外 fixtures,它是固定带行为的可执行文档。
  """
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
                 vision_cls_by_key: dict[int, str] | None = None,
                 lane_geo: LaneGeometry | None = None) -> list[Target]:
  """Build planner targets from radar points plus unmatched vision detections.

  ``confirmed_keys`` holds the :func:`radar_point_key` of the radar points that
  a vision detection confirmed, and ``vision_cls_by_key`` maps those keys to the
  vision class. Keys are ``trackId`` (stable across capnp re-iteration),
  never ``id()`` -- see :func:`radar_point_key`. Three things depend on them:

  * A radar point whose ground speed is near zero is kept ONLY when vision
    confirms it. Guardrails and bridge pillars sit at zero ground speed -- but
    so does a broken-down car, so a plain speed gate would discard a real
    hazard. Vision separates them: it reports a stopped car as ``car`` and does
    not report a guardrail as ``car``/``person``.
  * A confirmed radar point takes the vision class weight. Otherwise a
    radar-visible motorcycle is weighted 0.6 while one the radar missed is
    weighted 1.0 -- the better-perceived target counting for less.
  * ``lane_geo`` (C2) routes the in/out decision through :func:`gate_target`'s
    three tiers; ``None`` keeps the historical fixed-band behavior (tests,
    shadow proxy path).
  """
  confirmed = set(confirmed_keys)
  cls_by_key = vision_cls_by_key or {}
  targets: list[Target] = []
  for point in radar_points:
    dRel, yRel = float(point.dRel), float(point.yRel)
    key = radar_point_key(point)
    # vRel 缺失时保守按静止处理
    v_rel = getattr(point, "vRel", None)
    ground_speed = None if v_rel is None else abs(float(v_rel) + v_ego)
    if (ground_speed is None or ground_speed < C.STATIC_SPEED_THRESH) and key not in confirmed:
      continue
    cls = cls_by_key.get(key)
    in_gate, lane = gate_target(dRel, yRel, cls, lane_geo)
    if not in_gate:
      continue
    weight = C.class_weight(cls)
    targets.append(Target(side=_sign(yRel), dRel=dRel, yRel=yRel, w=weight, conf=1.0,
                           lane=lane, in_gate=True))
  for det in detections or []:
    try:
      dRel, yRel = float(det["dRel"]), float(det["yRel"])
    except (KeyError, TypeError, ValueError):
      continue
    cls = det.get("cls")
    in_gate, lane = gate_target(dRel, yRel, cls, lane_geo)
    if not in_gate:
      continue
    weight = C.class_weight(cls)
    targets.append(Target(side=_sign(yRel), dRel=dRel, yRel=yRel, w=weight,
                          conf=float(det.get("conf", 1.0)), lane=lane, in_gate=True))
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
  lane_geo: LaneGeometry | None = None  # C2/C7 geometry + quality flags (None = 模型几何不可用)


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
    model_v2 = sm['modelV2']
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
    # C2/C7: 车道几何一次提取,本帧所有目标判定共用（含遥测侧的 _target_rows）。
    lane_geo = lane_geometry(model_v2)
    targets = fuse_targets(radar.points, fused, v_ego=v_ego,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key,
                           lane_geo=lane_geo)
    return PerceptionFrame(radar_points=list(radar.points), detections=detections,
                           n_associated=n_associated, pairs=pairs,
                           confirmed_keys=confirmed_keys,
                           vision_cls_by_key=vision_cls_by_key, targets=targets,
                           lane_geo=lane_geo)
