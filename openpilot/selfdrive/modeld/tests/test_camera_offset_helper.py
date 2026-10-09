"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.common.transformations.camera import DEVICE_CAMERAS, view_frame_from_device_frame
from openpilot.common.transformations.model import get_warp_matrix
from openpilot.common.transformations.orientation import rot_from_euler
from openpilot.selfdrive.modeld.camera_offset_helper import CameraOffsetHelper
from openpilot.common.test import OpenpilotTestCase


class MockStruct:
  def __init__(self, **kwargs):
    for k, v in kwargs.items():
      setattr(self, k, v)


class TestCameraOffset(OpenpilotTestCase):
  def setup_method(self):
    self.camera_offset = CameraOffsetHelper()
    self.dc = DEVICE_CAMERAS[('mici', 'os04c10')]
    self.intrinsics_main = self.dc.narrow_road.intrinsics
    self.intrinsics_extra = self.dc.wide_road.intrinsics

  def warp_matrices(self, rpy_calib):
    device_from_calib_euler = np.array(rpy_calib, dtype=np.float32)
    main_transform = get_warp_matrix(device_from_calib_euler, self.intrinsics_main, False).astype(np.float32)
    extra_transform = get_warp_matrix(device_from_calib_euler, self.intrinsics_extra, True).astype(np.float32)
    return main_transform, extra_transform

  def test_smoothing(self):
    self.camera_offset.set_offset(0.2)
    calib = MockStruct(rpyCalib=[0.0, 0.0, 0.0], height=[1.22])
    main_transform, extra_transform = self.warp_matrices(calib.rpyCalib)

    self.camera_offset.update(main_transform, extra_transform, self.intrinsics_main, self.intrinsics_extra, calib)
    np.testing.assert_almost_equal(self.camera_offset.actual_camera_offset, 0.02)
    self.camera_offset.update(main_transform, extra_transform, self.intrinsics_main, self.intrinsics_extra, calib)
    np.testing.assert_almost_equal(self.camera_offset.actual_camera_offset, 0.038)

  def test_zero_offset_keeps_transforms_bit_exact(self):
    calib = MockStruct(rpyCalib=[0.0, np.radians(-3.0), 0.01], height=[1.3])
    main_transform, extra_transform = self.warp_matrices(calib.rpyCalib)

    main_out, extra_out = self.camera_offset.update(main_transform, extra_transform, self.intrinsics_main, self.intrinsics_extra, calib)
    np.testing.assert_array_equal(main_out, main_transform)
    np.testing.assert_array_equal(extra_out, extra_transform)

  def test_apply_camera_offset(self):
    v_horizon = CameraOffsetHelper.get_v_horizon(self.intrinsics_main, [])  # pitch = 0 fallback: v_horizon == cy
    transform = np.eye(3, dtype=np.float32)
    height = 1.22
    offset = 0.1

    expected_shear = np.eye(3, dtype=np.float32)
    expected_shear[0, 1] = offset / height
    expected_shear[0, 2] = -offset / height * v_horizon

    result = CameraOffsetHelper.apply_camera_offset(transform, height, offset, v_horizon)
    np.testing.assert_array_almost_equal(result, expected_shear)

  def test_v_horizon_empty_rpy(self):
    v_horizon = CameraOffsetHelper.get_v_horizon(self.intrinsics_main, [])
    np.testing.assert_almost_equal(v_horizon, self.intrinsics_main[1, 2])

  def test_v_horizon_projection(self):
    f, cy = self.intrinsics_main[1, 1], self.intrinsics_main[1, 2]

    for pitch_deg in [6.0, -6.0, 0.0]:
      rpy = [0.0, np.radians(pitch_deg), 0.0]
      d_dev = rot_from_euler(rpy) @ np.array([1.0, 0.0, 0.0])
      view = view_frame_from_device_frame @ d_dev
      expected = cy + f * view[1] / view[2]

      v_horizon = CameraOffsetHelper.get_v_horizon(self.intrinsics_main, rpy)
      np.testing.assert_almost_equal(v_horizon, expected, decimal=4)

  def test_update(self):
    height = 1.2
    pitch = np.radians(-8.0)
    calib = MockStruct(rpyCalib=[0.0, pitch, 0.0], height=[height])
    main_transform, extra_transform = self.warp_matrices(calib.rpyCalib)

    self.camera_offset.set_offset(0.1)
    main_out, extra_out = self.camera_offset.update(main_transform, extra_transform, self.intrinsics_main, self.intrinsics_extra, calib)
    assert not np.array_equal(main_out, main_transform)
    assert not np.array_equal(extra_out, extra_transform)

    # settle the low-pass filter
    for _ in range(100):
      main_out, extra_out = self.camera_offset.update(main_transform, extra_transform, self.intrinsics_main, self.intrinsics_extra, calib)

    # undo the warp matrix to get the shear matrix, per camera with its own intrinsics
    for out, transform, intrinsics in ((main_out, main_transform, self.intrinsics_main), (extra_out, extra_transform, self.intrinsics_extra)):
      shear = out @ np.linalg.inv(transform)
      expected_v_horizon = intrinsics[1, 2] - intrinsics[1, 1] * np.tan(pitch)
      np.testing.assert_almost_equal(shear[0, 1], self.camera_offset.actual_camera_offset / height, decimal=4)
      np.testing.assert_almost_equal(shear[0, 2], -self.camera_offset.actual_camera_offset / height * expected_v_horizon, decimal=3)

  def test_missing_height_falls_back(self):
    calib = MockStruct(rpyCalib=[0.0, 0.0, 0.0], height=[])
    main_transform, extra_transform = self.warp_matrices(calib.rpyCalib)

    self.camera_offset.set_offset(0.1)
    main_out, _ = self.camera_offset.update(main_transform, extra_transform, self.intrinsics_main, self.intrinsics_extra, calib)
    shear = main_out @ np.linalg.inv(main_transform)
    np.testing.assert_almost_equal(shear[0, 1], self.camera_offset.actual_camera_offset / 1.22, decimal=4)
