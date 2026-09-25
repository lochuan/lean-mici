"""C2 车道相对分类 + C7 置信门控测试（gate_target 三级判定）。

Frame 约定（ldw.py / relc.py / radard.py 三源验证）:
- 全链路车体系:雷达 yRel 左正,LaneGeometry 经 model_geometry 换算后
  y 左正、x 前保险杠原点（与 dRel 同参照）,gate_target 内无坐标换算。
- laneLines[1] = 本道左边界（左正 +y）,[2] = 本道右边界（-y）
- 推导速查（tier 1,左边界 L>0,car 半宽 hw=0.9,标准 3.5m 车道 L=1.75）:
  左侧目标侵入条件 yRel - hw < L  ⇔  yRel < L + hw = 2.65
"""


from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.perception import LaneGeometry, gate_target, lane_geometry


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
  geo = lane_geometry(md)
  assert geo is not None
  return geo


def _geo_no_lines(**kwargs) -> LaneGeometry:
  """边界置信不达标（tier 1 关闭）,路径可用的几何。"""
  return _geo(_MD(probs=(0.1, 0.1), **kwargs))


# --- tier 1: lane-relative ------------------------------------------------------

class TestTier1LaneRelative:
  def test_parked_car_butt_intruding_is_a_threat(self):
    """车屁股侵入:邻道停车边缘压线 —— 旧固定带（上界 2.5）漏掉的场景。"""
    geo = _geo(_MD())
    in_gate, lane = gate_target(20.0, 2.6, "car", geo)   # 2.6-0.9=1.7 已越过后推测的左边界
    assert in_gate is True and lane == -1

  def test_clean_adjacent_car_is_not_a_threat(self):
    """邻道正常行驶的车（车身不压线）:固定带内但物理上不碰我们车道。"""
    geo = _geo(_MD())
    # car 侵入条件 yRel < 2.65:2.8 起车身全在本道边界之外
    in_gate, lane = gate_target(20.0, 2.8, "car", geo)
    assert in_gate is False and lane == -1
    in_gate, lane = gate_target(20.0, 3.6, "car", geo)
    assert in_gate is False and lane == -1

  def test_vru_half_width_tightens_the_gate(self):
    """行人半宽 0.3:侵入条件收紧到 yRel < 2.05 —— 固定带 [1.2,2.5] 全收的老行为被物理边界修正。"""
    geo = _geo(_MD())
    in_gate, lane = gate_target(20.0, 2.0, "person", geo)   # 2.0-0.3=1.7 侵入
    assert in_gate is True and lane == -1
    in_gate, lane = gate_target(20.0, 2.2, "person", geo)   # 2.2-0.3=1.9 未侵入
    assert in_gate is False and lane == -1

  def test_truck_half_width_widens_intrusion(self):
    """卡车半宽 1.3:侵入条件放宽到 yRel < 3.05 —— 固定带外更远的目标也算压线。"""
    geo = _geo(_MD())
    in_gate, lane = gate_target(20.0, 2.9, "truck", geo)   # 2.9-1.3=1.6 侵入
    assert in_gate is True and lane == -1
    in_gate, lane = gate_target(20.0, 3.4, "truck", geo)   # 3.4-1.3=2.1 未侵入
    assert in_gate is False and lane == -1

  def test_centered_lead_is_never_a_threat(self):
    """本道正前方目标留给纵向/驾驶员:|yRel| < 1.2 不触发（中心偏出才有 0.35m 偏置的意义）。"""
    geo = _geo(_MD())
    in_gate, lane = gate_target(20.0, 0.8, "car", geo)
    assert in_gate is False and lane == 0
    in_gate, lane = gate_target(20.0, 1.1, "car", geo)
    assert in_gate is False and lane == 0

  def test_equivalence_with_old_band_on_standard_lane(self):
    """直道 3.5m 车道、car:新旧门控逐点等价
    （差异只在 2.5~2.65 的真实压线带和 VRU/卡车按半宽收紧/放宽的物理修正）。"""
    geo = _geo(_MD())
    for yRel in (1.3, 1.6, 2.0, 2.4):
      in_gate, _ = gate_target(20.0, yRel, "car", geo)
      assert in_gate is True, yRel
    for yRel in (0.9, 1.1, 2.8, 3.2):
      in_gate, _ = gate_target(20.0, yRel, "car", geo)
      assert in_gate is False, yRel

  def test_curved_boundary_follows_the_lane(self):
    """弯道:同一路径目标在不同距离上按弯的车道线分别判定 —— 固定带做不到。"""
    md = _MD()
    md.laneLines = [_Line(-1.75), _Line(-1.75, -2.6, -3.5), _Line(1.75), _Line(1.75)]
    geo = _geo(md)
    # 40m 处左边界弯到 -3.5:压线车 yRel=2.9（固定带上界外）,车身边缘 -2.0
    # 已越过 -3.5 的弯道边界 —— 侵入,威胁（固定带会漏掉:2.9 > 2.5）;中心
    # -2.9 也在弯道边界内侧,lane=0（本道）而非 -1。
    in_gate, lane = gate_target(40.0, 2.9, "car", geo)
    assert in_gate is True and lane == 0
    # 5m 处边界还是 -1.75:同一 yRel 的车边缘 -2.0 未侵入;中心在边界外 lane=-1。
    in_gate, lane = gate_target(5.0, 2.9, "car", geo)
    assert in_gate is False and lane == -1

  def test_gate_samples_lane_geometry_at_bumper_distance(self):
    """门限在**车体系距离**上采样车道几何（T2/票 #3 的行为修正）。

    左边界相机系 x=(5,20,40)、y=(-1.75,-2.0,-4.0)（右正）。几何经
    model_geometry 换算后 x 整体前移一个安装偏移 1.5m（相机在保险杠后方）:
    dRel=20 采样到的是相机系 21.5 处的插值 2.15,而非 20 处的 2.0。
    car 半宽 0.9:yRel=3.0 的车身边缘 2.1 —— 换算后判「侵入」,
    未换算的旧混系采样在此处判「未侵入」。本测试只接受换算后语义。
    """
    md = _MD()
    md.laneLines = [_Line(-1.75), _Line(-1.75, -2.0, -4.0), _Line(1.75), _Line(1.75)]
    geo = _geo(md)
    in_gate, lane = gate_target(20.0, 3.0, "car", geo)
    assert in_gate is True and lane == -1

  def test_per_side_validity(self):
    """左边界磨损 -> 左侧目标回退 tier 2;右侧目标仍走 tier 1。"""
    geo = _geo(_MD(stds=(0.9, 0.1)))
    assert geo.left_valid is False and geo.right_valid is True
    in_gate, lane = gate_target(20.0, -2.0, "car", geo)   # 右侧目标走 tier 1: 2.0-0.9=1.1 侵入
    assert in_gate is True and lane == 1


# --- tier 2: path-relative -------------------------------------------------------

class TestTier2PathRelative:
  def _curved_path_geo(self) -> LaneGeometry:
    # 边界置信不达标 + 左弯路径（车体系左正 y 随距离增大）,position.yStd 达标
    return LaneGeometry(left_valid=False, right_valid=False,
                        left_x=(5.0, 40.0), left_y=(1.75, 1.75),
                        right_x=(5.0, 40.0), right_y=(-1.75, -1.75),
                        path_x=(5.0, 20.0, 40.0), path_y=(1.0, 2.2, 4.0),
                        path_std=(0.1, 0.1, 0.1))

  def test_curve_same_lane_lead_excluded(self):
    """弯道上的同车道前车:固定带把它当侧向威胁,路径相对正确排除。

    路径在 40m 处弯到 yRel=4.0（左）;沿着路径行驶的前车 yRel≈4.0 —— 相对
    路径的偏差 ≈ 0,不是威胁。
    """
    geo = self._curved_path_geo()
    in_gate, lane = gate_target(40.0, 4.0, "car", geo)     # 路径上
    assert in_gate is False and lane == 0

  def test_curve_same_car_rel_coord_changes_with_distance(self):
    """同一车体系 yRel=2.0 的目标,弯道上按距离分别判定 —— 固定带做不到。

    路径 20m 处 2.2（左）:偏差 -0.2,还在路径附近（排除）;40m 处 4.0:偏差
    -2.0,目标已在路径右外侧 2m,压到带内（威胁,lane=+1 右侧）。
    """
    geo = self._curved_path_geo()
    in_gate, lane = gate_target(20.0, 2.0, "car", geo)
    assert in_gate is False and lane == 0
    in_gate, lane = gate_target(40.0, 2.0, "car", geo)
    assert in_gate is True and lane == 1

  def test_curve_off_path_target_in_band(self):
    """弯道上的侧向威胁:相对路径偏差落在 [1.2, 2.5] 带内才触发。"""
    geo = self._curved_path_geo()
    # 20m 处路径 2.2（左）:目标车体系 yRel=4.5(左) -> 偏差 = 4.5-2.2 = 2.3,带内
    in_gate, lane = gate_target(20.0, 4.5, "car", geo)
    assert in_gate is True and lane == -1
    # 偏差过远（邻道正常车流）:yRel=7.0 -> 偏差 = 7.0-2.2 = 4.8 > 2.5
    in_gate, lane = gate_target(20.0, 7.0, "car", geo)
    assert in_gate is False and lane == -1

  def test_high_path_std_falls_to_fixed_band(self):
    """position.yStd 超标 -> 路径不可信 -> 固定带（tier 3,旧行为）。"""
    geo = LaneGeometry(left_valid=False, right_valid=False,
                       left_x=(), left_y=(), right_x=(), right_y=(),
                       path_x=(5.0, 40.0), path_y=(0.0, 0.0), path_std=(0.9, 0.9))
    in_gate, _ = gate_target(20.0, 2.0, "car", geo)    # 固定带内 -> 威胁
    assert in_gate is True
    in_gate, _ = gate_target(20.0, 3.0, "car", geo)    # 固定带外 -> 非威胁
    assert in_gate is False


# --- tier 3: fixed band -----------------------------------------------------------

class TestTier3FixedBand:
  def test_none_geo_keeps_historical_band(self):
    """模型几何整体不可用 -> 逐点复现旧固定带行为。"""
    for yRel, expected in ((1.0, False), (1.5, True), (2.0, True), (2.6, False), (-1.5, True)):
      in_gate, lane = gate_target(20.0, yRel, "car", None)
      assert in_gate is expected, yRel
      assert lane == ((-1 if yRel > 0 else 1) if expected else 0), yRel

  def test_distance_gate_unchanged(self):
    geo = _geo(_MD())
    assert gate_target(0.0, 1.8, "car", geo)[0] is False
    assert gate_target(C.D_GATE + 1.0, 1.8, "car", geo)[0] is False
    assert gate_target(C.D_GATE, 1.8, "car", geo)[0] is True

  def test_duck_typed_model_returns_none(self):
    """缺属性的假 modelV2（daemon 测试桩形态）安全回退,绝不起异常。"""
    class _Bare:
      action = None
    assert lane_geometry(_Bare()) is None
    assert lane_geometry(None) is None


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
    assert lane_geometry(md) is None
    md2 = _MD()
    md2.position = None
    assert lane_geometry(md2) is None

  def test_path_std_missing_disables_tier2(self):
    """position 桩没有 yStd 时 path_std=() -> tier 2 安全关闭。"""
    md = _MD()
    del md.position.yStd
    geo = lane_geometry(md)
    assert geo is not None and geo.path_std == ()
