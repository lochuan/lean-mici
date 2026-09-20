import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal.services import SERVICE_LIST
from openpilot.system.manager import process_config as pc

OPENPILOT = Path(__file__).resolve().parents[3]

CRUISE_BUTTONS_BOOL_PARAMS = (
  ("CruiseButtonsEnabled", '"0"'),
  ("CruiseButtonsCurve", '"1"'),
  ("CruiseButtonsLead", '"1"'),
  ("CruiseButtonsStandstill", '"1"'),
)


def _param_attrs(key: str) -> list[str]:
  """Parse a params_keys.h entry into its [flags, type, default?] parts."""
  keys = (OPENPILOT / "common/params_keys.h").read_text()
  m = re.search(r'\{"' + re.escape(key) + r'", \{([^}]*)\}\}', keys)
  assert m is not None, f"{key} not registered in params_keys.h"
  return [part.strip() for part in m.group(1).split(",")]


class _FakeParams:
  def __init__(self, enabled: bool):
    self._enabled = enabled

  def get_bool(self, key: str) -> bool:
    assert key == "CruiseButtonsEnabled"
    return self._enabled


def test_cruise_buttons_params_registered():
  keys = (OPENPILOT / "common/params_keys.h").read_text()
  for key, _ in CRUISE_BUTTONS_BOOL_PARAMS:
    assert f'"{key}"' in keys
  assert '"CruiseButtonsAccel"' in keys


def test_cruise_buttons_enabled_defaults_explicitly_off():
  # Gate defaults off so the stub process never starts until the user opts in.
  assert _param_attrs("CruiseButtonsEnabled")[-1] == '"0"'


def test_cruise_buttons_param_types_and_flags():
  for key, default in CRUISE_BUTTONS_BOOL_PARAMS:
    attrs = _param_attrs(key)
    assert attrs[1] == "BOOL"
    assert attrs[-1] == default
    assert "PERSISTENT" in attrs[0] and "BACKUP" in attrs[0]
  accel = _param_attrs("CruiseButtonsAccel")
  assert accel[1] == "FLOAT"
  assert "PERSISTENT" in accel[0] and "BACKUP" not in accel[0]
  assert len(accel) == 2  # FLOAT entries carry no default string


def test_cruise_buttons_debug_service_is_20hz():
  assert SERVICE_LIST["cruiseButtonsDebug"].frequency == 20.0


def test_cruise_buttons_debug_capnp_registered():
  custom = (OPENPILOT / "cereal/custom.capnp").read_text()
  assert "struct CruiseButtonsDebug @0xd4e8a1c6b3f29057" in custom
  log = (OPENPILOT / "cereal/log.capnp").read_text()
  assert re.search(r"cruiseButtonsDebug @\d+ :Custom\.CruiseButtonsDebug;", log)


def test_cruisebuttond_process_registered_with_gate():
  assert "cruisebuttond" in pc.managed_processes
  proc = pc.managed_processes["cruisebuttond"]
  assert proc.module == "openpilot.selfdrive.cruisebuttond.cruisebuttond"
  assert proc.should_run is pc.cruise_buttons_run


def test_cruise_buttons_run_requires_onroad_car_and_enabled():
  cp = SimpleNamespace(notCar=False)
  assert pc.cruise_buttons_run(True, _FakeParams(True), cp) is True
  assert pc.cruise_buttons_run(True, _FakeParams(False), cp) is False
  assert pc.cruise_buttons_run(False, _FakeParams(True), cp) is False
  assert pc.cruise_buttons_run(True, _FakeParams(True), SimpleNamespace(notCar=True)) is False