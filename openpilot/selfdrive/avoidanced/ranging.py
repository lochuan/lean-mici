"""单目测距与方位角。

关键前提:单目相机能准确测**方位角**,不能准确测**距离**。方位角只取决于内参
和 yaw/roll,与 pitch 和地平面假设无关;而地平面投影的距离在 40m 处对 0.5deg
的 pitch 误差就放大到 41%。所以关联按方位角做,距离优先取雷达,雷达漏检的目标
才退回框高测距(同样不依赖 pitch)。
"""

from __future__ import annotations

import math

from openpilot.selfdrive.avoidanced.constants import CLASS_HEIGHTS_M, TRUNCATION_MARGIN_PX


def range_from_box_height(h_px: float, fy: float, cls: str) -> float | None:
  """针孔关系 ``d = fy * H / h_px``。类别未知或框高非正时返回 ``None``。"""
  height = CLASS_HEIGHTS_M.get(cls)
  if height is None or h_px <= 0.0 or fy <= 0.0:
    return None
  return fy * height / h_px


def bearing_from_pixel(u_full: float, cx: float, fx: float, yaw: float) -> float:
  """水平方位角(rad),车体坐标系,向图像右为正。

  ``yaw`` 是标定出的相机偏航(正 = 相机朝左看),需从测量值里减掉。
  """
  return math.atan2(u_full - cx, fx) - yaw


def is_truncated(y2_full: float, frame_height: float) -> bool:
  """框底是否触到(或接近)图像下边界。"""
  return y2_full >= frame_height - TRUNCATION_MARGIN_PX
