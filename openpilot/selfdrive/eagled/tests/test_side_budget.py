"""C9 侧向预算测试:side_pictures 的间隙物理 + fuse_objects 全量对象语义。

物理模型: budget = (|yRel| - 目标半宽) - EGO_HALF_WIDTH - SIDE_MARGIN,
即偏置后仍保留 SIDE_MARGIN 的车身间隙才放行。哨兵 BUDGET_UNCONSTRAINED
(999.0) 表示该侧无侧向约束。
"""

import pytest

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.perception import Target, fuse_objects, side_pictures


def _obj(yRel, dRel=20.0, cls=None, vRel=0.0):
  return Target(side=1 if yRel > 0 else -1, dRel=dRel, yRel=yRel,
               w=C.class_weight(cls), conf=1.0, cls=cls, vRel=vRel)


class TestSidePictures:
  def test_unconstrained_side_returns_sentinel(self):
    left, right = side_pictures([], bsm_left=False, bsm_right=False)
    assert left.budget == C.BUDGET_UNCONSTRAINED and left.lead is None
    assert right.budget == C.BUDGET_UNCONSTRAINED and right.lead is None

  def test_bsm_forces_zero_without_a_lead(self):
    # 布尔报警没有可指向的目标:预算 0,lead None
    left, right = side_pictures([_obj(3.0, cls="car")], bsm_left=True, bsm_right=False)
    assert left.budget == 0.0 and left.lead is None
    assert right.budget != 0.0   # 右侧不受左 BSM 影响

  def test_car_gap_physics(self):
    # car 半宽 0.9:budget = (3.0-0.9) - 0.9 - 0.3 = 0.9
    left, _ = side_pictures([_obj(3.0, cls="car")], False, False)
    assert left.budget == pytest.approx(0.9)
    assert left.lead is not None and left.lead.cls == "car"

  def test_truck_half_width_tightens(self):
    # 卡车半宽 1.3 同一位置:budget = 3.0-1.3-0.9-0.3 = 0.5 < 0.9
    left, _ = side_pictures([_obj(3.0, cls="truck")], False, False)
    assert left.budget == pytest.approx(0.5)

  def test_unknown_cls_uses_default_half_width(self):
    # 纯雷达未关联目标:默认半宽 0.5 -> budget = 3.0-0.5-0.9-0.3 = 1.3
    left, _ = side_pictures([_obj(3.0)], False, False)
    assert left.budget == pytest.approx(1.3)

  def test_nearest_constraining_object_wins(self):
    near = _obj(2.2, cls="car")     # budget = 2.2-0.9-0.9-0.3 = 0.1
    far = _obj(4.0, cls="car")      # budget = 4.0-0.9-0.9-0.3 = 1.9
    left, _ = side_pictures([far, near], False, False)
    assert left.budget == pytest.approx(0.1) and left.lead is near

  def test_in_gate_threat_also_constrains_its_side(self):
    # 本道威胁侧一样参与预算:向威胁侧偏置必须被间隙物理拦下
    # VRU 半宽 0.3:budget = (1.5-0.3) - 0.9 - 0.3 = 0.0
    left, _ = side_pictures([_obj(1.5, cls="person")], False, False)
    assert left.budget == 0.0

  def test_sides_are_independent(self):
    left, right = side_pictures([_obj(-3.0, cls="car")], False, False)
    assert left.budget == C.BUDGET_UNCONSTRAINED
    assert right.budget == pytest.approx(0.9) and right.lead is not None

  def test_lateral_window_excludes_far_lanes(self):
    # |yRel| > SIDE_MAX_Y(4.5):两个车道开外,不参与
    left, _ = side_pictures([_obj(4.6, cls="car")], False, False)
    assert left.budget == C.BUDGET_UNCONSTRAINED

  def test_longitudinal_window_excludes_distant_objects(self):
    # dRel > SIDE_WINDOW_D(60):远处的邻道车不参与偏置折算
    left, _ = side_pictures([_obj(3.0, dRel=61.0, cls="car")], False, False)
    assert left.budget == C.BUDGET_UNCONSTRAINED
    left, _ = side_pictures([_obj(3.0, dRel=C.SIDE_WINDOW_D, cls="car")], False, False)
    assert left.budget == pytest.approx(0.9)

  def test_zero_drel_excluded(self):
    left, _ = side_pictures([_obj(3.0, dRel=0.0, cls="car")], False, False)
    assert left.budget == C.BUDGET_UNCONSTRAINED


class TestFuseObjects:
  def test_returns_all_objects_with_verdicts(self):
    # 带外目标保留(in_gate=False),带内威胁判进 —— 预算正是要吃带外的邻道车流
    pts = [type("P", (), {"dRel": 20.0, "yRel": 3.0, "vRel": 0.0, "trackId": 1})(),
           type("P", (), {"dRel": 20.0, "yRel": -1.8, "vRel": 0.0, "trackId": 2})()]
    objs = fuse_objects(pts, v_ego=20.0)
    assert len(objs) == 2
    by_side = {o.side: o for o in objs}
    assert by_side[1].in_gate is False      # |yRel|=3.0 带外(tier 3)
    assert by_side[-1].in_gate is True       # 1.8 带内威胁
    targets = [o for o in objs if o.in_gate]
    assert len(targets) == 1

  def test_confirmed_cls_and_vrel_ride_on_target(self):
    pts = [type("P", (), {"dRel": 20.0, "yRel": -1.8, "vRel": -3.5, "trackId": 7})()]
    objs = fuse_objects(pts, v_ego=20.0, confirmed_keys=(7,), vision_cls_by_key={7: "person"})
    assert objs[0].cls == "person" and objs[0].vRel == -3.5
    assert objs[0].w == C.VRU_WEIGHT

  def test_vision_only_object_is_unknown_speed(self):
    # 视觉独有目标(雷达没关联上)速度未知 -> None。
    # 注:缺 vRel 属性的雷达点走更早的一道门 —— "vRel 缺失保守按静止",
    # 未确认即被丢弃,根本进不了对象列表;所以速度未知只可能来自视觉。
    objs = fuse_objects([], detections=[{"dRel": 20.0, "yRel": 3.0, "cls": "car", "conf": 0.9}], v_ego=20.0)
    assert len(objs) == 1
    assert objs[0].vRel is None and objs[0].cls == "car"


class TestChangeClear:
  """变道清空判定:carrotpilot 4s/3s 时间投影 + 近区硬拦 + 速度未知不放宽。"""

  V_EGO = 20.0

  def _left(self, objects, bsm=False):
    left, _ = side_pictures(objects, bsm, False, v_ego=self.V_EGO)
    return left

  def test_far_and_fast_side_car_is_cleared(self):
    # 用户点名场景:邻道远而快的车放行 —— vRel=+5(比我们快),30m 外
    obj = _obj(3.0, dRel=30.0, cls="car", vRel=5.0)
    left = self._left([obj])
    assert left.change_clear is True
    assert left.budget < C.BUDGET_UNCONSTRAINED   # 预算仍被压(避让消费),但变道放行

  def test_same_speed_far_object_is_cleared(self):
    # 同速远车(30m):4s 后它 30+80=110 > 我们 60,放行
    obj = _obj(3.0, dRel=30.0, cls="car", vRel=0.0)
    assert self._left([obj]).change_clear is True

  def test_slow_close_object_blocks(self):
    # 慢车(vLead=5,合速差 15m/s)20m 外:20+20=40 < 60,拦
    obj = _obj(3.0, dRel=20.0, cls="car", vRel=-15.0)
    assert self._left([obj]).change_clear is False

  def test_near_zone_blocks_regardless_of_speed(self):
    # 近区硬拦:贴身快车(6m 内)即使投影放行也拦 —— BSM 覆盖不到的前角
    obj = _obj(3.0, dRel=C.LANE_CHANGE_NEAR_D, cls="car", vRel=5.0)
    assert self._left([obj]).change_clear is False

  def test_unknown_speed_never_relaxes(self):
    # 速度未知(视觉独有/雷达缺字段):无论多远多"快"都不放宽 ——
    # 雷达没测到速度的自行车按同速放行是危险方向
    obj = _obj(3.0, dRel=50.0, cls="bicycle", vRel=None)
    assert self._left([obj]).change_clear is False

  def test_bsm_side_is_not_clear(self):
    obj = _obj(3.0, dRel=30.0, cls="car", vRel=5.0)
    assert self._left([obj], bsm=True).change_clear is False

  def test_empty_side_is_clear(self):
    assert self._left([]).change_clear is True

  def test_oncoming_collapses_the_projection(self):
    # 对向车(vLead=-20)投影急剧收缩:50m 外也被拦 —— C4 对向场景的伏笔
    obj = _obj(3.0, dRel=50.0, cls="car", vRel=-40.0)
    assert self._left([obj]).change_clear is False

  def test_projection_boundary_equality_blocks(self):
    # carrotpilot 严格比较:侧车 4s 位置 == 我们 3s 位置 -> 拦(对方须多跑 1s)
    # vLead=5(vRel=-15), vEgo=20: dRel + 20 vs 60;dRel=40 -> 相等 -> 拦
    obj = _obj(3.0, dRel=40.0, cls="car", vRel=-15.0)
    assert self._left([obj]).change_clear is False
    obj = _obj(3.0, dRel=41.0, cls="car", vRel=-15.0)
    assert self._left([obj]).change_clear is True

  def test_one_conflicting_object_blocks_the_side(self):
    # 清空是"全部满足":一个投影冲突的目标就拦整侧
    far_fast = _obj(3.0, dRel=50.0, cls="car", vRel=5.0)
    slow_close = _obj(3.0, dRel=15.0, cls="car", vRel=-15.0)
    assert self._left([far_fast, slow_close]).change_clear is False

  def test_sides_are_independent_for_clear(self):
    obj = _obj(-3.0, dRel=15.0, cls="car", vRel=-15.0)   # 右侧慢车
    _, right = side_pictures([obj], False, False, v_ego=self.V_EGO)
    left, _ = side_pictures([obj], False, False, v_ego=self.V_EGO)
    assert right.change_clear is False
    assert left.change_clear is True   # 左侧无目标,清空
