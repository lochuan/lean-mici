"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.common.transformations.camera import view_frame_from_device_frame
from openpilot.common.transformations.orientation import rot_from_euler

DEFAULT_HEIGHT = 1.22  # m, camera height above ground


class GroundProjector:
  """Wide camera pixel <-> road frame ground point.

  Road frame: x forward, y left, z up; ground at z=0, camera at height above it.
  The calib frame (level device-style frame at the camera) relates to the road
  frame by diag(1, -1, -1) with the ground plane at z_calib = height.
  R_wc = view_frame_from_device_frame @ rot(wideFromDeviceEuler) @ rot(rpyCalib)
  maps calib -> view (per openpilot/selfdrive/ui/onroad/augmented_road_view.py).
  The small physical offset between the wide and main cameras is ignored.
  """

  def __init__(self, intrinsics, rpy_calib, wide_from_device_euler, height: float = DEFAULT_HEIGHT):
    self.K = np.asarray(intrinsics, dtype=np.float64)
    self.K_inv = np.linalg.inv(self.K)
    self.R_wc = view_frame_from_device_frame @ rot_from_euler(wide_from_device_euler) @ rot_from_euler(rpy_calib)
    self.height = float(height)

  def pixel_to_ground(self, u: float, v: float) -> tuple[float, float] | None:
    ray_view = self.K_inv @ np.array([u, v, 1.0])
    ray_calib = self.R_wc.T @ ray_view
    if ray_calib[2] < 1e-6:
      return None
    t = self.height / ray_calib[2]
    return float(t * ray_calib[0]), float(-t * ray_calib[1])

  def ground_to_pixel(self, x: float, y: float) -> tuple[float, float] | None:
    p_calib = np.array([x, -y, self.height])
    p_view = self.R_wc @ p_calib
    if p_view[2] <= 0.0:
      return None
    h = self.K @ p_view
    return float(h[0] / h[2]), float(h[1] / h[2])
