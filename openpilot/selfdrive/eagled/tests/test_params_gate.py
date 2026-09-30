import re
from pathlib import Path
from types import SimpleNamespace

import pytest

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
  # C9+ 可调参（lanlink stepper 驱动 constants.apply_param_overrides）
  for key in ("AvoidanceSideMargin", "AvoidanceEgoHalfWidth", "AvoidanceLaneProbMin",
              "AvoidanceLaneStdMax", "LaneChangeNearZone"):
    assert f'"{key}"' in keys, key


def test_param_overrides_apply_and_restore():
  """constants.apply_param_overrides:覆盖生效、缺键恢复默认、钳制起作用。"""
  from openpilot.selfdrive.eagled import constants as C

  class _Params:
    def __init__(self, values: dict):
      self._values = values

    def get(self, key, block=False, return_default=False):
      return self._values.get(key)

  defaults = {name: getattr(C, name) for _, (name, _, _, _) in C._PARAM_OVERRIDABLE.items()}
  try:
    C.apply_param_overrides(_Params({"AvoidanceSideMargin": "0.8", "LaneChangeNearZone": "99"}))
    assert C.SIDE_MARGIN == pytest.approx(0.8)
    assert C.LANE_CHANGE_NEAR_D == pytest.approx(20.0)   # 钳到 hi=20
    # 其余无键项恢复编译期默认
    assert C.EGO_HALF_WIDTH == pytest.approx(defaults["EGO_HALF_WIDTH"])
    # 全空 -> 全部恢复
    C.apply_param_overrides(_Params({}))
    for name, value in defaults.items():
      assert getattr(C, name) == pytest.approx(value), name
  finally:
    C.apply_param_overrides(_Params({}))   # 不污染其他测试


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
