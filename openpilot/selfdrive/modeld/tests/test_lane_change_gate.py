import time
from types import SimpleNamespace

import openpilot.cereal.messaging as messaging
from openpilot.selfdrive.modeld.lane_change_gate import run_lane_change_gate


class FakeSm(dict):
  def __init__(self, left="clear", right="clear", age_s=0.1, seen=True, valid=True):
    super().__init__(
      eagleState=SimpleNamespace(changeClearLeftState=left, changeClearRightState=right),
      carState="car", carControl=SimpleNamespace(latActive=True))
    self.seen = {"eagleState": seen}
    self.valid = {"eagleState": valid}
    self.recv_time = {"eagleState": time.monotonic() - age_s}


class FakeDesireHelper:
  lane_change_state = 2
  lane_change_direction = 1
  lane_turn_direction = 3
  block_left = 4
  block_right = 5
  hold_reason = 6

  def update(self, car_state, lat_active, lane_change_prob, left_edge, right_edge, change_clear_left, change_clear_right):
    self.call = (car_state, lat_active, lane_change_prob, left_edge, right_edge, change_clear_left, change_clear_right)


def run(sm):
  dh = FakeDesireHelper()
  modelv2 = messaging.new_message('modelV2').modelV2
  mdv2sp = messaging.new_message('modelDataV2SP').modelDataV2SP
  run_lane_change_gate(dh, sm, 0.25, 1.5, 2.5, modelv2, mdv2sp)
  return dh, modelv2, mdv2sp


def test_fresh_eagle_state_maps_to_clear_flags():
  dh, _, _ = run(FakeSm(left="blocked", right="clear"))
  assert dh.call == ("car", True, 0.25, 1.5, 2.5, False, True)


def test_unknown_clear_state_does_not_participate_in_gating():
  dh, _, _ = run(FakeSm(left="unknown", right="blocked"))
  assert dh.call[-2:] == (None, False)


def test_stale_never_seen_or_invalid_eagle_state_falls_back_to_none():
  for sm in (FakeSm(left="blocked", age_s=10.), FakeSm(left="blocked", seen=False), FakeSm(left="blocked", valid=False)):
    dh, _, _ = run(sm)
    assert dh.call[-2:] == (None, None)


def test_desire_helper_outputs_are_published():
  _, modelv2, mdv2sp = run(FakeSm())
  assert modelv2.meta.laneChangeState == 2
  assert modelv2.meta.laneChangeDirection == 1
  assert mdv2sp.laneTurnDirection == 3
  assert mdv2sp.leftLaneChangeBlock == 4
  assert mdv2sp.rightLaneChangeBlock == 5
  assert mdv2sp.laneChangeHoldReason == 6
