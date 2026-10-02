"""C7 车道几何提取 + 置信门测试（lane_geometry）。

Frame 约定（ldw.py / relc.py / radard.py 三源验证）:
- 全链路车体系:雷达 yRel 左正,LaneGeometry 经 model_geometry 换算后
  y 左正、x 前保险杠原点（与 dRel 同参照）。
- laneLines[1] = 本道左边界（左正 +y）,[2] = 本道右边界（-y）
"""


from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.perception import LaneGeometry, lane_geometry


# --- fixtures ------------------------------------------------------------------


class _Line:
  """车道线桩:x 等距;y 传单值（常数线）或多个值（弯线）。"""

  def __init__(self, *ys, x=(5.0, 20.0, 40.0)):
    self.x = [float(v) for v in x]
    self.y = [float(y) for y in ys] if len(ys) > 1 else [float(ys[0])] * len(self.x)


class _Pos:
  """position 桩:路径 y 可为常数或弯线,yStd 恒定。"""

  def __init__(self, ys=0.0, std=0.1, x=(5.0, 20.0, 40.0)):
    self.x = [float(v) for v in x]
    self.y = [float(v) for v in ys] if isinstance(ys, (list, tuple)) else [float(ys)] * len(self.x)
    self.yStd = [float(std)] * len(self.x)


class _MD:
  """modelV2 桩:标准 3.5m 车道（左边界 -1.75,右边界 +1.75）,直路径。"""

  def __init__(self, left_y=-1.75, right_y=1.75, probs=(0.9, 0.9), stds=(0.1, 0.1),
               path_ys=0.0, path_std=0.1):
    self.laneLines = [_Line(left_y), _Line(left_y), _Line(right_y), _Line(right_y)]
    self.laneLineProbs = [0.5, probs[0], probs[1], 0.5]  # [1]=左,[2]=右
    self.laneLineStds = [0.5, stds[0], stds[1], 0.5]
    self.position = _Pos(path_ys, path_std)


def _geo(md) -> LaneGeometry:
  geo = lane_geometry(md, C.CAMERA_TO_FRONT)
  assert geo is not None
  return geo


# --- lane_geometry 质量门 ----------------------------------------------------------

class TestLaneGeometryExtraction:
  def test_prob_and_std_gates(self):
    assert _geo(_MD()).left_valid is True
    assert _geo(_MD(probs=(0.59, 0.9))).left_valid is False   # 概率差一线
    assert _geo(_MD(stds=(0.31, 0.1))).left_valid is False    # 方差超一线
    assert _geo(_MD(probs=(0.9, 0.59))).right_valid is False

  def test_missing_lists_return_none(self):
    md = _MD()
    md.laneLines = []
    assert lane_geometry(md, C.CAMERA_TO_FRONT) is None
    md2 = _MD()
    md2.position = None
    assert lane_geometry(md2, C.CAMERA_TO_FRONT) is None

  def test_duck_typed_model_returns_none(self):
    """缺属性的假 modelV2（daemon 测试桩形态）安全回退,绝不起异常。"""
    class _Bare:
      action = None
    assert lane_geometry(_Bare(), C.CAMERA_TO_FRONT) is None
    assert lane_geometry(None, C.CAMERA_TO_FRONT) is None

  def test_path_std_missing_gives_empty_path_std(self):
    """position 桩没有 yStd 时 path_std=()。"""
    md = _MD()
    del md.position.yStd
    geo = lane_geometry(md, C.CAMERA_TO_FRONT)
    assert geo is not None and geo.path_std == ()
