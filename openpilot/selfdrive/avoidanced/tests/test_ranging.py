import pytest
from openpilot.selfdrive.avoidanced.ranging import is_truncated, range_from_box_height

FY = 425.25


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


# bearing_from_pixel(像素列方位角,绕相机光心)已删除:它与雷达侧的保险杠原点
# 方位角差一个视差角,混用曾同时污染匹配和未匹配检测的 yRel(见
# test_association.py 的视差回归测试)。方位角现在一律由投影后的车体坐标
# atan2(-yRel, dRel) 推出,钉在 test_projection.py 的
# test_projected_bearing_is_the_bumper_origin_bearing。


def test_truncated_box_is_rejected():
  assert is_truncated(760.0, 760.0)
  assert is_truncated(757.0, 760.0)          # 在 margin 内
  assert not is_truncated(700.0, 760.0)
