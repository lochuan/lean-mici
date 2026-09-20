"""Camera -> vehicle-frame projection for YOLO detections.

A detection box's bottom-centre pixel is unprojected through the pinhole model
and intersected with the ground plane below the camera. The output is the car
frame shared with the planner and the radar: ``dRel`` forward from the FRONT
BUMPER (the radar's dRel origin, see toyota radar_interface.py) and ``yRel``
lateral with left positive. Because the windshield camera sits behind the
bumper, its projected forward distance is larger and ``CAMERA_TO_FRONT`` is
subtracted to align with radar dRel (spec §4 radar-camera extrinsics; pitch and
yaw start at 0 and are refined by the P0 calibration).

The ROI bookkeeping also lives here: YOLO boxes are in 640x384 ROI pixels, but
projection must use the original full-frame pixel coordinates, so every ROI
point is inverse-mapped through :class:`RoiMeta` first.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from openpilot.selfdrive.avoidanced.constants import CAMERA_PITCH, CAMERA_TO_FRONT, CAMERA_YAW, ROI_HORIZON_MARGIN, ROI_MODE, ROI_MODE_NATIVE, VEHICLE_WEIGHT, VRU_CLASSES, VRU_WEIGHT
from openpilot.selfdrive.avoidanced.yolo_detector import INPUT_H, INPUT_W


@dataclass(frozen=True)
class RoiMeta:
  """Maps ROI (640x384) pixel coordinates back to full-frame pixels."""

  scale_u: float   # full-frame px per ROI px, horizontal
  scale_v: float   # full-frame px per ROI px, vertical
  offset_v: float  # full-frame row of the ROI top edge
  # NOTE: appended with a default on purpose. RoiMeta is built positionally in
  # tests/test_shadow.py and tests/test_daemon_fusion.py; inserting a field
  # ahead of offset_v would silently shift those arguments.
  offset_u: float = 0.0  # full-frame column of the ROI left edge


def roi_meta_for(width: float, height: float, mode: str = ROI_MODE,
                 horizon_row: float | None = None) -> RoiMeta:
  """ROI geometry for a full frame.

  ``ROI_MODE_SQUASH`` keeps the historical behaviour: crop to the YOLO aspect
  from the bottom, then resize. On a 1344x760 frame the crop is a no-op and the
  whole frame is squashed into 640x384.

  ``ROI_MODE_NATIVE`` takes a 1:1 640x384 window, centred horizontally and
  positioned vertically from ``horizon_row`` (the calibrated horizon; falls back
  to the frame centre). No resampling, so a distant object keeps twice the
  pixels it has under SQUASH.
  """
  if mode == ROI_MODE_NATIVE:
    row = height / 2.0 if horizon_row is None else horizon_row
    top = min(max(0.0, row - ROI_HORIZON_MARGIN), max(0.0, height - INPUT_H))
    left = max(0.0, (width - INPUT_W) / 2.0)
    return RoiMeta(scale_u=1.0, scale_v=1.0, offset_v=top, offset_u=left)

  crop_h = min(height, round(width * INPUT_H / INPUT_W))
  return RoiMeta(scale_u=width / INPUT_W, scale_v=crop_h / INPUT_H,
                 offset_v=height - crop_h, offset_u=0.0)


def roi_to_full(u: float, v: float, meta: RoiMeta) -> tuple[float, float]:
  """ROI pixel -> original full-frame pixel."""
  return u * meta.scale_u + meta.offset_u, v * meta.scale_v + meta.offset_v


def project_box_to_vehicle(u: float, v: float, fx: float, fy: float, cx: float, cy: float,
                           height: float, pitch: float, yaw: float = CAMERA_YAW,
                           camera_to_front: float = 0.0) -> dict | None:
  """Box bottom-centre pixel -> car-frame ground point, ``None`` if the ray never hits the ground.

  ``pitch`` is positive with the camera tilted down, ``yaw`` positive looking
  left; both are mount constants (initially 0, refined by P0 calibration).
  ``camera_to_front`` shifts the origin from the windshield camera back to the
  radar's front-bumper origin (subtracted, see module docstring); callers that
  want radar-aligned ``dRel`` pass ``CAMERA_TO_FRONT``.
  """
  x_n = (u - cx) / fx
  y_n = (v - cy) / fy
  # Ray in the level vehicle frame (x forward, y left, z up); camera pitched
  # down by ``pitch``: forward axis (cos p, 0, -sin p), right axis (0, -1, 0).
  cos_p, sin_p = math.cos(pitch), math.sin(pitch)
  d_x = cos_p - y_n * sin_p
  d_y = -x_n
  d_z = -y_n * cos_p - sin_p
  if d_z >= 0.0:
    return None  # ray points above the horizon: never reaches the ground plane
  t = -height / d_z
  x, y = t * d_x, t * d_y
  # Undo the camera yaw (positive = camera looks left) in the ground plane.
  cos_y, sin_y = math.cos(yaw), math.sin(yaw)
  x_v = x * cos_y - y * sin_y
  y_v = x * sin_y + y * cos_y
  return {"dRel": x_v - camera_to_front, "yRel": y_v}


def project_detections(dets: Iterable[dict] | None, fx: float, fy: float, cx: float, cy: float,
                       height: float, pitch: float = CAMERA_PITCH, yaw: float = CAMERA_YAW,
                       camera_to_front: float = CAMERA_TO_FRONT,
                       roi_meta: RoiMeta | None = None) -> list[dict]:
  """YOLO ROI boxes -> car-frame detections for ``fuse_targets`` / ``associate``.

  Each box's bottom-centre is inverse-mapped from ROI pixels to full-frame
  pixels (projection must use full-frame coordinates, not ROI coords), then
  projected onto the ground plane. Boxes whose ray never reaches the ground are
  dropped. ``w`` is the planner class weight (VRU > vehicle).
  """
  out: list[dict] = []
  for det in dets or []:
    u = (float(det["x1"]) + float(det["x2"])) / 2.0
    v = float(det["y2"])
    if roi_meta is not None:
      u, v = roi_to_full(u, v, roi_meta)
    point = project_box_to_vehicle(u=u, v=v, fx=fx, fy=fy, cx=cx, cy=cy, height=height,
                                   pitch=pitch, yaw=yaw, camera_to_front=camera_to_front)
    if point is None:
      continue
    cls = det.get("cls")
    out.append({
      "dRel": point["dRel"],
      "yRel": point["yRel"],
      "cls": cls,
      "conf": float(det.get("conf", 1.0)),
      "w": VRU_WEIGHT if cls in VRU_CLASSES else VEHICLE_WEIGHT,
    })
  return out
