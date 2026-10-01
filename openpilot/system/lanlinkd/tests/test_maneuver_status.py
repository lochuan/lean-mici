"""maneuver_status 纯函数：每个原因一条 + 优先级。"""
import pytest

from openpilot.system.lanlinkd.maneuver_status import avoid_block_reason, lane_change_status

DBG = {"active": True, "valid": False, "maxOffset": 0.5, "vEgo": 20.0, "edgeClearance": 999.0}


def _avoid(dbg=None, enabled=True, steering=False, lc="off"):
  return avoid_block_reason({**DBG, **(dbg or {})}, enabled, steering, lc)


def test_avoid_none_when_inactive_or_executing():
  assert _avoid({"active": False}) is None
  assert _avoid({"valid": True}) is None


@pytest.mark.parametrize("kwargs,expected", [
  ({"enabled": False}, "disabled"),
  ({"steering": True}, "steering"),
  ({"lc": "laneChangeStarting"}, "laneChange"),
  ({"dbg": {"vEgo": 3.0}}, "speed"),
  ({"dbg": {"vEgo": 40.0}}, "speed"),
  ({"dbg": {"edgeClearance": 0.2}}, "edge"),
  ({"dbg": {"maxOffset": 0.0}}, "budget"),
  ({}, "unknown"),
])
def test_avoid_reasons(kwargs, expected):
  assert _avoid(**kwargs) == expected


def test_avoid_priority_disabled_over_steering():
  assert _avoid(enabled=False, steering=True) == "disabled"


CAR = {"leftBlinker": True, "rightBlinker": False, "leftBlindspot": False, "rightBlindspot": False,
       "brakePressed": False, "steeringPressed": False, "vEgo": 20.0}


def _lc(state="preLaneChange", direction="left", car=None, edge=(False, False), clear=(True, True), alc=2):
  return lane_change_status(state, direction, {**CAR, **(car or {})}, edge, clear, alc)["blockReason"]


@pytest.mark.parametrize("kwargs,expected", [
  ({"car": {"leftBlindspot": True}}, "blindspot"),
  ({"edge": (True, False)}, "roadEdge"),
  ({"clear": (False, True)}, "notClear"),
  ({"alc": 0}, "nudge"),
  ({"car": {"brakePressed": True}}, "brake"),
  ({}, "waiting"),
  ({"state": "off", "alc": -1}, "alcOff"),
  ({"state": "off", "car": {"vEgo": 3.0}}, "belowSpeed"),
  ({"state": "off"}, "notArmed"),
  ({"state": "laneChangeStarting"}, None),
  ({"state": "off", "car": {"leftBlinker": False}}, None),
])
def test_lane_change_reasons(kwargs, expected):
  assert _lc(**kwargs) == expected


def test_lane_change_only_checks_target_side():
  assert _lc(car={"rightBlindspot": True}, edge=(False, True), clear=(True, False)) == "waiting"


def test_lane_change_unknown_clear_does_not_block():
  assert _lc(clear=(None, None)) == "waiting"
