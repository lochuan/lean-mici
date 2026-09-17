from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal.services import SERVICE_LIST
from openpilot.system.manager import process_config as pc

OPENPILOT = Path(__file__).resolve().parents[3]


class _FakeParams:
  def __init__(self, enabled: bool):
    self._enabled = enabled

  def get_bool(self, key: str) -> bool:
    assert key == "AvoidanceEnabled"
    return self._enabled


def test_avoidance_params_registered():
  keys = (OPENPILOT / "common/params_keys.h").read_text()
  assert '"AvoidanceEnabled"' in keys
  assert '"AvoidanceMinConfidence"' in keys
  assert '"AvoidanceMaxLateralOffset"' in keys


def test_lateral_maneuver_plan_service_is_5hz():
  assert SERVICE_LIST["lateralManeuverPlan"].frequency == 5.0


def test_avoidanced_process_registered_with_gate():
  assert "avoidanced" in pc.managed_processes
  proc = pc.managed_processes["avoidanced"]
  assert proc.module == "openpilot.selfdrive.avoidanced.avoidanced"
  assert proc.should_run is pc.avoidance_run


def test_avoidance_run_requires_onroad_car_and_enabled():
  cp = SimpleNamespace(notCar=False)
  assert pc.avoidance_run(True, _FakeParams(True), cp) is True
  assert pc.avoidance_run(True, _FakeParams(False), cp) is False
  assert pc.avoidance_run(False, _FakeParams(True), cp) is False
  assert pc.avoidance_run(True, _FakeParams(True), SimpleNamespace(notCar=True)) is False
