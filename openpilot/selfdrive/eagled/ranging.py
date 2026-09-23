"""单目测距与方位角。

关键前提:单目相机能准确测**方位角**,不能准确测**距离**。方位角对 pitch 只有
二阶敏感(射线方位 ≈ atan(x_n),pitch 修正在 O(pitch·y_n) 量级);而地平面投影
的距离在 40m 处对 0.5deg 的 pitch 误差就放大到 41%。所以关联按方位角做,距离
优先取雷达,雷达漏检的目标才退回框高测距(同样不依赖 pitch)。

方位角一律取**保险杠原点**的 ``atan2(-yRel, dRel)``,由投影后的车体坐标推出
(见 ``projection.project_detections``),与雷达侧 ``association._radar_bearing``
同一参照。不要用像素列 ``atan2(u-cx, fx)``:那个角绕的是相机光心,而相机在
保险杠后方 ``CAMERA_TO_FRONT`` 处 —— 混用两个原点会缩小未匹配检测的 yRel 并
给匹配注入假 Δbearing(本分支修掉过的视差 bug)。
"""

from __future__ import annotations

from openpilot.selfdrive.eagled.constants import CLASS_HEIGHTS_M, TRUNCATION_MARGIN_PX


def range_from_box_height(h_px: float, fy: float, cls: str) -> float | None:
  """针孔关系 ``d = fy * H / h_px``。类别未知或框高非正时返回 ``None``。

  返回的是**相机系**距离:相机装在风挡上、位于前保险杠之后,与雷达 dRel(保险杠
  系)比较前必须减 ``CAMERA_TO_FRONT`` —— 换算点在 ``association._box_height_range_bumper``,
  不要在别处再单独做一次。
  """
  height = CLASS_HEIGHTS_M.get(cls)
  if height is None or h_px <= 0.0 or fy <= 0.0:
    return None
  return fy * height / h_px


def is_truncated(y2_full: float, frame_height: float) -> bool:
  """框底是否触到(或接近)图像下边界。"""
  return y2_full >= frame_height - TRUNCATION_MARGIN_PX
