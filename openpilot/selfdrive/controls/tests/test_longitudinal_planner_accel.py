import pytest

from opendbc.car.interfaces import ACCEL_MAX
from openpilot.selfdrive.controls.lib.longitudinal_planner import A_CRUISE_MIN, get_cruise_accel


@pytest.mark.parametrize('cruise,ego,previous,dt,expected', [
  (20.0, 0.0, 0.0, 0.05, 0.08),
  (50.0, 40.0, 0.0, 0.05, 0.03),
  (50.0, 0.0, 0.0, 10.0, ACCEL_MAX),
  (0.0, 30.0, 0.0, 10.0, A_CRUISE_MIN),
  (0.0, 10.0, 0.5, 0.1, 0.38),
])
def test_cruise_acceleration_and_jerk_bounds(cruise, ego, previous, dt, expected):
  assert get_cruise_accel(cruise, ego, previous, dt) == pytest.approx(expected)
