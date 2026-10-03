"""desire_helper 变道启动否决:BSM 盲区与 relc 路沿两路,仅拦启动,starting 之后不复查。"""

import pytest

from openpilot.cereal import custom, log
from openpilot.common.params import Params
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper

LaneChangeState = log.LaneChangeState
TurnDirection = custom.ModelDataV2SP.TurnDirection


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


class TestVeto:
  def test_torque_starts_lane_change(self, dh):
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_any_single_veto_blocks(self, dh):
    for cs_kwargs, dh_kwargs in (({"bsm_left": True}, {}), ({}, {"left_edge_detected": True})):
      _to_pre(dh)
      dh.update(_cs(torque=0.5, **cs_kwargs), True, 0.0, **dh_kwargs)
      assert dh.lane_change_state == LaneChangeState.preLaneChange, (cs_kwargs, dh_kwargs)

  def test_other_side_veto_does_not_block(self, dh):
    _to_pre(dh)
    dh.update(_cs(torque=0.5, bsm_right=True), True, 0.0, right_edge_detected=True)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_starting_survives_veto_appearing(self, dh):
    _to_pre(dh)
    dh.update(_cs(torque=0.5), True, 0.0)
    dh.update(_cs(torque=0.5, bsm_left=True), True, 0.5, left_edge_detected=True)
    assert dh.lane_change_state == LaneChangeState.laneChangeStarting
