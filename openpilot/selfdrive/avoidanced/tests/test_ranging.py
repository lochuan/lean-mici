import pytest
from openpilot.selfdrive.avoidanced.ranging import (bearing_from_pixel, is_truncated,
                                                    range_from_box_height)

FX = FY = 425.25
CX = 672.0


def test_box_height_range_is_the_pinhole_relation():
  # 1.7m 行人在 20m 处:h_px = fy * 1.7 / 20
  h_px = FY * 1.7 / 20.0
  assert range_from_box_height(h_px, FY, "person") == pytest.approx(20.0, rel=1e-6)


def test_box_height_range_uses_the_class_height():
  h_px = FY * 3.2 / 30.0
  assert range_from_box_height(h_px, FY, "truck") == pytest.approx(30.0, rel=1e-6)


def test_box_height_range_rejects_degenerate_input():
  assert range_from_box_height(0.0, FY, "person") is None
  assert range_from_box_height(-5.0, FY, "person") is None
  assert range_from_box_height(10.0, FY, "unknown_class") is None


def test_bearing_is_zero_on_the_optical_axis():
  assert bearing_from_pixel(CX, CX, FX, yaw=0.0) == pytest.approx(0.0)


def test_bearing_is_positive_to_the_image_right():
  assert bearing_from_pixel(CX + 100, CX, FX, yaw=0.0) > 0.0


def test_bearing_subtracts_camera_yaw():
  raw = bearing_from_pixel(CX + 100, CX, FX, yaw=0.0)
  assert bearing_from_pixel(CX + 100, CX, FX, yaw=0.05) == pytest.approx(raw - 0.05)


def test_truncated_box_is_rejected():
  assert is_truncated(760.0, 760.0)
  assert is_truncated(757.0, 760.0)          # 在 margin 内
  assert not is_truncated(700.0, 760.0)
