"""SCC-V param 清理的回归测试(Task 6):stock-ACC Toyota 启用巡航按钮控制时,
SmartCruiseControlVision 不被 _cleanup_unsupported_params 删除(cruisebuttond
的弯道减速场景消费 SCC-V 的 vTarget)。"""
from unittest.mock import MagicMock

from opendbc.car.structs import car
from openpilot.cereal import custom
from openpilot.sunnypilot.selfdrive.car.interfaces import _cleanup_unsupported_params


def _params(cruise_buttons=False):
  p = MagicMock()
  p._gb = cruise_buttons
  p.get_bool.side_effect = lambda key: p._gb if key == "CruiseButtonsEnabled" else False
  return p


def _cp_and_sp():
  CP = car.CarParams(openpilotLongitudinalControl=False, pcmCruise=True, brand="toyota")
  CP_SP = custom.CarParamsSP(pcmCruiseSpeed=True)
  return CP, CP_SP


def test_scc_vision_kept_on_stock_acc_with_cruise_buttons():
  CP, CP_SP = _cp_and_sp()
  params = _params(cruise_buttons=True)
  _cleanup_unsupported_params(CP, CP_SP, params)
  removed = [c.args[0] for c in params.remove.call_args_list]
  assert "SmartCruiseControlVision" not in removed
  assert "SmartCruiseControlMap" in removed  # 地图场景无消费方,照常清理


def test_scc_vision_removed_without_cruise_buttons():
  CP, CP_SP = _cp_and_sp()
  params = _params(cruise_buttons=False)
  _cleanup_unsupported_params(CP, CP_SP, params)
  removed = [c.args[0] for c in params.remove.call_args_list]
  assert "SmartCruiseControlVision" in removed
