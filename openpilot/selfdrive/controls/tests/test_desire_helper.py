"""C9 变道预算门测试:desire_helper 消费 eagleState 的每侧横向预算。

三路否决各管一段:BSM 布尔(eagleState 缺失时的兜底)、relc 边缘(目标道
不存在——预算不含路沿)、预算(邻道清空折算,None=不参与)。仅拦启动,
starting 之后不复查(与上游 BSM 行为一致)。
"""

import pytest

from openpilot.cereal import custom, log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.desire_helper import LANE_CHANGE_CLEAR_BUDGET, DesireHelper

LaneChangeState = log.LaneChangeState
TurnDirection = custom.ModelDataV2SP.TurnDirection

BUDGET_CLEAR = 999.0     # eagleState 哨兵:该侧无侧向约束
BUDGET_BLOCKED = 2.4     # 邻道窗内任何车都会压到的量级


class _NS:
  def __init__(self, **kwargs):
    self.__dict__.update(kwargs)


def _cs(left_blinker=True, right_blinker=False, torque=0.0, v=20.0,
        bsm_left=False, bsm_right=False, brake=False):
  return _NS(leftBlinker=left_blinker, rightBlinker=right_blinker, vEgo=v,
             steeringPressed=torque != 0.0, steeringTorque=torque,
             leftBlindspot=bsm_left, rightBlindspot=bsm_right, brakePressed=brake)


@pytest.fixture
def dh(tmp_path, monkeypatch):
  import openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change as alc_mod
  import openpilot.sunnypilot.selfdrive.controls.lib.lane_turn_desire as ltd_mod
  params = Params(str(tmp_path))
  monkeypatch.setattr(alc_mod, "Params", lambda: params)
  monkeypatch.setattr(ltd_mod, "Params", lambda: params)

  helper = DesireHelper()
  # 哑元化 ALC/LaneTurn:构造本身已被 tmp Params 隔离,这里只去掉分支对
  # 其内部状态机的依赖,让测试聚焦预算门。
  helper.alc = _NS(update_params=lambda: None,
                   update_lane_change=lambda blocked, brake: None,
                   update_state=lambda: None,
                   auto_lane_change_allowed=False,
                   lane_change_set_timer=alc_mod.AutoLaneChangeMode.NUDGE)
  helper.lane_turn_controller = _NS(update_params=lambda: None,
                                    update_lane_turn=lambda **kwargs: None,
                                    get_turn_direction=lambda: TurnDirection.none)
  return helper


def _to_pre(helper, left=True):
  """两次 update 造出 preLaneChange:先清 blinker 边沿,再打灯。"""
  helper.update(_cs(left_blinker=False, right_blinker=False), True, 0.0)
  helper.update(_cs(left_blinker=left, right_blinker=not left), True, 0.0)
  assert helper.lane_change_state == LaneChangeState.preLaneChange
  return helper


class TestBudgetGate:
  def test_clear_budget_allows_starting(self, dh):
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, budget_left=BUDGET_CLEAR, budget_right=BUDGET_CLEAR)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_blocked_budget_prevents_starting(self, dh):
    _to_pre(dh)
    # 打灯向左:目标道是左道,左预算不足即拦(2.4 < 3.0)
    dh.update(_cs(torque=0.5), True, 0.0, budget_left=BUDGET_BLOCKED, budget_right=BUDGET_CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange   # 没启动

  def test_budget_zero_blocks_like_bsm(self, dh):
    # BSM 报警映射为预算 0(eagleState 侧的同一语义):必须拦
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, budget_left=0.0, budget_right=BUDGET_CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange

  def test_gate_is_direction_specific(self, dh):
    # 向左变道只看左预算;右预算不足不拦(远离侧)
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, budget_left=BUDGET_CLEAR, budget_right=BUDGET_BLOCKED)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_gate_is_direction_specific_mirrored(self, dh):
    # 向右变道:右预算不足拦
    _to_pre(dh, left=False)
    dh.update(_cs(left_blinker=False, right_blinker=True, torque=-0.5), True, 0.0,
              budget_left=BUDGET_CLEAR, budget_right=BUDGET_BLOCKED)
    assert dh.lane_change_state == LaneChangeState.preLaneChange


class TestFallbacks:
  def test_none_budget_keeps_legacy_bsm_behavior(self, dh):
    # eagleState 缺失/过期 -> 预算 None -> 不参与门控,扭矩照常启动
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_bsm_bool_still_blocks_with_none_budget(self, dh):
    # 兜底门:预算缺失时 BSM 布尔是唯一门控,必须继续工作
    _to_pre(dh)
    dh.update(_cs(torque=0.5, bsm_left=True), True, 0.0)
    assert dh.lane_change_state == LaneChangeState.preLaneChange

  def test_relc_edge_still_blocks_with_clear_budget(self, dh):
    # relc 边缘(目标道不存在)与预算正交:预算 999 时仍必须拦
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, left_edge_detected=True,
              budget_left=BUDGET_CLEAR, budget_right=BUDGET_CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange


class TestVetoStack:
  def test_any_single_veto_blocks(self, dh):
    # 三路否决叠加:任一路为真都不启动(BSM / relc 边缘 / 预算)
    for cs_kwargs, dh_kwargs in (({"bsm_left": True}, {}),            # BSM 走 carstate
                                  ({}, {"left_edge_detected": True}),  # relc 边缘
                                  ({}, {"budget_left": 1.0})):         # 预算
      _to_pre(dh)
      budgets = {"budget_left": BUDGET_CLEAR, "budget_right": BUDGET_CLEAR}
      budgets.update(dh_kwargs)
      dh.update(_cs(torque=0.5, **cs_kwargs), True, 0.0, **budgets)
      assert dh.lane_change_state == LaneChangeState.preLaneChange, (cs_kwargs, dh_kwargs)


class TestNoRecheckAfterStarting:
  def test_starting_survives_budget_drop(self, dh):
    # 仅拦启动:starting 之后预算掉到 0 也不回退(与上游 BSM 不复查一致)。
    # lane_change_prob 保持高(0.5)避免 starting -> pre 的概率回退路径。
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, budget_left=BUDGET_CLEAR, budget_right=BUDGET_CLEAR)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
    dh.update(_cs(torque=0.5), True, 0.5, budget_left=0.0, budget_right=0.0)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting


def test_threshold_is_the_lane_clear_boundary():
  # 3.0 的论证:邻道窗内任何车都把预算压到 ≤2.4(4.5-0.9-0.9-0.3),全清 999。
  assert LANE_CHANGE_CLEAR_BUDGET > 4.5 - 0.9 - 0.9 - 0.3   # 窗内最远 car 的预算上界
  assert LANE_CHANGE_CLEAR_BUDGET < 999.0
