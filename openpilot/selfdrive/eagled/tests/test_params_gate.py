import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal.services import SERVICE_LIST
from openpilot.system.manager import process_config as pc

OPENPILOT = Path(__file__).resolve().parents[3]


def _param_attrs(key: str) -> list[str]:
  """Parse a params_keys.h entry into its [flags, type, default?] parts."""
  keys = (OPENPILOT / "common/params_keys.h").read_text()
  m = re.search(r'\{"' + re.escape(key) + r'", \{([^}]*)\}\}', keys)
  assert m is not None, f"{key} not registered in params_keys.h"
  return [part.strip() for part in m.group(1).split(",")]


def test_avoidance_params_registered():
  keys = (OPENPILOT / "common/params_keys.h").read_text()
  assert '"AvoidanceEnabled"' in keys
  assert '"AvoidanceMinConfidence"' in keys
  assert '"AvoidanceMaxLateralOffset"' in keys


def test_avoidance_enabled_defaults_explicitly_off():
  # Missing-value semantics already read as False, but the default must be
  # explicit so a fresh install materialises "0" like every other gated BOOL.
  assert _param_attrs("AvoidanceEnabled")[-1] == '"0"'


def test_avoidance_param_types_and_flags():
  enabled = _param_attrs("AvoidanceEnabled")
  assert enabled[1] == "BOOL"
  assert "PERSISTENT" in enabled[0] and "BACKUP" in enabled[0]
  for key in ("AvoidanceMaxLateralOffset", "AvoidanceMinConfidence"):
    attrs = _param_attrs(key)
    assert attrs[1] == "FLOAT"
    assert "PERSISTENT" in attrs[0]
    assert "BACKUP" not in attrs[0]


def test_lateral_maneuver_plan_service_is_5hz():
  assert SERVICE_LIST["lateralManeuverPlan"].frequency == 5.0


def test_eagled_process_registered_with_gate():
  assert "eagled" in pc.managed_processes
  proc = pc.managed_processes["eagled"]
  assert proc.module == "openpilot.selfdrive.eagled.eagled"
  assert proc.should_run is pc.eagle_run


def test_eagle_run_requires_onroad_car_only():
  """感知层默认开启：AvoidanceEnabled 不再门控进程，只门控进程内的避让执行。"""
  cp = SimpleNamespace(notCar=False)
  params = object()  # eagle_run 不读 params；感知永远随 onroad+car 走
  assert pc.eagle_run(True, params, cp) is True
  assert pc.eagle_run(False, params, cp) is False
  assert pc.eagle_run(True, params, SimpleNamespace(notCar=True)) is False
