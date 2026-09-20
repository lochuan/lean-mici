"""Shaper 测试:统一公式(spec §3)与死区边界。全部场景都是公式的特例。"""

import pytest

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.shaper import ShaperInputs, should_adjust, target_set_speed


def inp(ceiling=100.0, set_speed=60.0, v_ego=60.0, scc=None, lead=None, standstill=False):
  return ShaperInputs(ceiling, set_speed, v_ego, scc, lead, standstill)


# --- target_set_speed:统一公式 ---


def test_target_ceiling_only():
  assert target_set_speed(inp()) == 100.0


def test_target_none_when_ceiling_unknown():
  assert target_set_speed(inp(ceiling=None)) is None


def test_lead_pulls_target_down_with_margin():
  assert target_set_speed(inp(lead=40.0)) == 40.0 + C.MARGIN_KPH


def test_scc_pulls_target_down():
  assert target_set_speed(inp(scc=45.0)) == 45.0


def test_target_is_min_of_all_candidates():
  assert target_set_speed(inp(scc=50.0, lead=45.0)) == 45.0 + C.MARGIN_KPH


def test_floor_clamps_target_above_ceiling():
  assert target_set_speed(inp(ceiling=20.0)) == C.FLOOR_KPH


def test_ceiling_above_floor_passes_through():
  assert target_set_speed(inp(ceiling=35.0)) == 35.0


# --- should_adjust:死区 ---


def test_adjust_up():
  assert should_adjust(60.0, 70.0, held_s=10.0) == ("up", 10.0)


def test_adjust_down_delta_is_absolute():
  assert should_adjust(70.0, 60.0, held_s=10.0) == ("down", 10.0)


def test_deadband_exactly_at_is_noop():
  assert should_adjust(60.0, 65.0, held_s=10.0) is None
  assert should_adjust(65.0, 60.0, held_s=10.0) is None


def test_deadband_just_outside_adjusts():
  d = C.DEADBAND_KPH + 1e-3
  assert should_adjust(60.0, 60.0 + d, 10.0) == ("up", pytest.approx(d))
  assert should_adjust(60.0 + d, 60.0, 10.0) == ("down", pytest.approx(d))


def test_deadband_just_inside_is_noop():
  d = C.DEADBAND_KPH - 1e-3
  assert should_adjust(60.0, 60.0 + d, 10.0) is None
  assert should_adjust(60.0 + d, 60.0, 10.0) is None


def test_held_time_below_deadband_s_is_noop():
  assert should_adjust(60.0, 80.0, held_s=C.DEADBAND_S - 0.1) is None


def test_held_time_exactly_deadband_s_adjusts():
  assert should_adjust(60.0, 80.0, held_s=C.DEADBAND_S) == ("up", 20.0)
