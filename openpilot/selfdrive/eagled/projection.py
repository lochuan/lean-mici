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

from openpilot.selfdrive.eagled.constants import CAMERA_PITCH, CAMERA_YAW, ROI_HORIZON_MARGIN, ROI_MODE, ROI_MODE_NATIVE, class_weight
from openpilot.selfdrive.eagled.ranging import is_truncated
from openpilot.selfdrive.eagled.yolo_detector import INPUT_H, INPUT_W


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


@dataclass(frozen=True)
class CalibratedGeometry:
  """openpilot 的 device->road 外参,来自 ``extrinsicsCalibration``。

  ``valid=False`` 意味着视觉路径必须整体关掉:地平面投影的 dRel 对 pitch 的
  敏感度在 40m 处是 0.5deg -> 41%,用未标定的 pitch 会直接生成虚假的大幅偏移。
  """

  valid: bool
  roll: float
  pitch: float
  yaw: float


UNCALIBRATED = CalibratedGeometry(False, 0.0, 0.0, 0.0)


def geometry_from_calibration(msg, valid: bool) -> CalibratedGeometry:
  """``extrinsicsCalibration`` -> CalibratedGeometry. 任何疑点都判为无效。"""
  if not valid or str(getattr(msg, "calStatus", "")) != "calibrated":
    return UNCALIBRATED
  rpy = list(getattr(msg, "rpyCalib", []) or [])
  if len(rpy) != 3:
    return UNCALIBRATED
  return CalibratedGeometry(True, float(rpy[0]), float(rpy[1]), float(rpy[2]))


def horizon_row_for(cy: float, fy: float, geom: CalibratedGeometry) -> float:
  """标定 pitch 下地平线所在的全帧行。未标定时退回光心行。

  负号是物理正确的,不要"修"回正号:openpilot 的 rpyCalib pitch 为正表示相机
  下俯(calibrationd.py 的 observed_rpy 拟合,device 系 z 朝下),相机下俯时
  地平线在图像中上移(行号变小)。与 project_box_to_vehicle 的射线方程一致:
  地平线掠射射线 d_z = 0 -> y_n = -tan(pitch) -> v = cy - fy*tan(pitch)。
  """
  if not geom.valid:
    return cy
  return cy - fy * math.tan(geom.pitch)


def project_box_to_vehicle(u: float, v: float, fx: float, fy: float, cx: float, cy: float,
                           height: float, pitch: float, yaw: float = CAMERA_YAW,
                           roll: float = 0.0, camera_to_front: float = 0.0) -> dict | None:
  """Box bottom-centre pixel -> car-frame ground point, ``None`` if the ray never hits the ground.

  ``pitch`` is positive with the camera tilted down, ``yaw`` positive looking
  left; both are mount constants (initially 0, refined by P0 calibration).
  ``roll`` undoes camera roll about the optical axis (from live calibration).
  ``camera_to_front`` shifts the origin from the windshield camera back to the
  radar's front-bumper origin (subtracted, see module docstring); callers that
  want radar-aligned ``dRel`` pass ``CAMERA_TO_FRONT``.
  """
  x_n = (u - cx) / fx
  y_n = (v - cy) / fy
  # Undo camera roll about the optical axis before building the ray: roll mixes
  # horizontal displacement into the vertical component, which the ground-plane
  # intersection would otherwise read as a distance error.
  if roll:
    cr, sr = math.cos(roll), math.sin(roll)
    x_n, y_n = x_n * cr + y_n * sr, -x_n * sr + y_n * cr
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
                       roll: float = 0.0, *, camera_to_front: float,
                       roi_meta: RoiMeta | None = None,
                       frame_height: float | None = None) -> list[dict]:
  """YOLO ROI boxes -> car-frame detections for ``fuse_targets`` / ``associate``.

  Each box's bottom-centre is inverse-mapped from ROI pixels to full-frame
  pixels (projection must use full-frame coordinates, not ROI coords), then
  projected onto the ground plane. Boxes whose ray never reaches the ground are
  dropped. ``w`` is the planner class weight (VRU > vehicle).

  ``bearing`` is the horizontal bearing about the FRONT-BUMPER origin,
  ``atan2(-yRel, dRel)`` on the projected car-frame point — the SAME origin and
  sign convention as the radar side (``association._radar_bearing``), positive
  toward image right. It must NOT be computed from the pixel column
  (``atan2(u - cx, fx)``): that angle is about the CAMERA's optical centre, and
  the camera sits ``CAMERA_TO_FRONT`` behind the bumper — mixing the two
  origins shrank unmatched ``yRel`` by ``d / (d + CAMERA_TO_FRONT)`` and injected
  a spurious Δbearing into matching (the parallax bug this replaced). Yaw is
  already undone inside :func:`project_box_to_vehicle`'s ground-plane rotation.
  ``dRelSource`` tags where ``dRel`` came from (``"ground"`` here). Boxes whose
  bottom edge touches the frame bottom (``frame_height``) are truncated — their
  y2 is not the true ground contact — and are rejected when ``frame_height`` is
  given. ``boxHeightPx`` is the full-frame box height for box-height ranging.
  """
  out: list[dict] = []
  for det in dets or []:
    u = (float(det["x1"]) + float(det["x2"])) / 2.0
    v = float(det["y2"])
    if roi_meta is not None:
      u, v = roi_to_full(u, v, roi_meta)
    if frame_height is not None and is_truncated(v, frame_height):
      continue
    point = project_box_to_vehicle(u=u, v=v, fx=fx, fy=fy, cx=cx, cy=cy, height=height,
                                   pitch=pitch, yaw=yaw, roll=roll, camera_to_front=camera_to_front)
    if point is None:
      continue
    cls = det.get("cls")
    out.append({
      "dRel": point["dRel"],
      "yRel": point["yRel"],
      "cls": cls,
      "conf": float(det.get("conf", 1.0)),
      "w": class_weight(cls),
      # 保险杠原点方位角(与雷达同参照),从投影后的车体坐标推出 —— 不要改回像素列。
      "bearing": math.atan2(-point["yRel"], point["dRel"]),
      "dRelSource": "ground",
      "boxHeightPx": abs(float(det["y2"]) - float(det["y1"])) * (roi_meta.scale_v if roi_meta else 1.0),
    })
  return out
