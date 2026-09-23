"""C9+ 变道清空门测试:desire_helper 消费 eagleState 的 changeClearLeft/Right。

三路否决各管一段:BSM 布尔(eagleState 缺失时的兜底)、relc 边缘(目标道
不存在——清空判定不含路沿)、清空标志(eagled 的时间投影:近区/速度未知
拦,远而快的侧车放行,None=不参与)。仅拦启动,starting 之后不复查。
"""

import pytest

from openpilot.cereal import custom, log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper

LaneChangeState = log.LaneChangeState
TurnDirection = custom.ModelDataV2SP.TurnDirection

CLEAR = True             # eagleState.changeClear*:该目标道可进
NOT_CLEAR = False         # 近区/速度未知/投影冲突/BSM 任一不满足


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
  def test_clear_flag_allows_starting(self, dh):
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, change_clear_left=CLEAR, change_clear_right=CLEAR)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_not_clear_flag_prevents_starting(self, dh):
    _to_pre(dh)
    # 打灯向左:目标道是左道,左清空标志 False 即拦
    dh.update(_cs(torque=0.5), True, 0.0, change_clear_left=NOT_CLEAR, change_clear_right=CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange   # 没启动

  def test_not_clear_blocks_like_bsm(self, dh):
    # BSM 报警在 eagled 侧即映射为不清空:必须拦
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, change_clear_left=NOT_CLEAR, change_clear_right=CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange

  def test_gate_is_direction_specific(self, dh):
    # 向左变道只看左预算;右预算不足不拦(远离侧)
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, change_clear_left=CLEAR, change_clear_right=NOT_CLEAR)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_gate_is_direction_specific_mirrored(self, dh):
    # 向右变道:右预算不足拦
    _to_pre(dh, left=False)
    dh.update(_cs(left_blinker=False, right_blinker=True, torque=-0.5), True, 0.0,
              change_clear_left=CLEAR, change_clear_right=NOT_CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange


class TestFallbacks:
  def test_none_clear_keeps_legacy_bsm_behavior(self, dh):
    # eagleState 缺失/过期 -> 清空标志 None -> 不参与门控,扭矩照常启动
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_bsm_bool_still_blocks_with_none_clear(self, dh):
    # 兜底门:清空标志缺失时 BSM 布尔是唯一门控,必须继续工作
    _to_pre(dh)
    dh.update(_cs(torque=0.5, bsm_left=True), True, 0.0)
    assert dh.lane_change_state == LaneChangeState.preLaneChange

  def test_relc_edge_still_blocks_with_clear_flag(self, dh):
    # relc 边缘(目标道不存在)与清空判定正交:清空 True 时仍必须拦
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, left_edge_detected=True,
              change_clear_left=CLEAR, change_clear_right=CLEAR)
    assert dh.lane_change_state == LaneChangeState.preLaneChange


class TestVetoStack:
  def test_any_single_veto_blocks(self, dh):
    # 三路否决叠加:任一路为真都不启动(BSM / relc 边缘 / 清空标志)
    for cs_kwargs, dh_kwargs in (({"bsm_left": True}, {}),              # BSM 走 carstate
                                  ({}, {"left_edge_detected": True}),    # relc 边缘
                                  ({}, {"change_clear_left": NOT_CLEAR})):  # 清空标志
      _to_pre(dh)
      clears = {"change_clear_left": CLEAR, "change_clear_right": CLEAR}
      clears.update(dh_kwargs)
      dh.update(_cs(torque=0.5, **cs_kwargs), True, 0.0, **clears)
      assert dh.lane_change_state == LaneChangeState.preLaneChange, (cs_kwargs, dh_kwargs)


class TestNoRecheckAfterStarting:
  def test_starting_survives_clear_drop(self, dh):
    # 仅拦启动:starting 之后清空标志掉 False 也不回退(与上游 BSM 不复查一致)。
    # lane_change_prob 保持高(0.5)避免 starting -> pre 的概率回退路径。
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0, change_clear_left=CLEAR, change_clear_right=CLEAR)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
    dh.update(_cs(torque=0.5), True, 0.5, change_clear_left=NOT_CLEAR, change_clear_right=NOT_CLEAR)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting


# 清空语义(eagled side_pictures)在 test_side_budget.py 的 TestChangeClear
# 中钉死;desire_helper 只消费布尔,无本地阈值。
