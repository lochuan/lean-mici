"""S1b：manager 上路/下路切换时清 mem Params（/dev/shm/params）里的 SCC-map 键。"""
from openpilot.common.params import Params, ParamKeyFlag
from openpilot.system.manager import map_params


def test_clear_map_params_removes_position_and_targets(tmp_path):
  # 临时目录当 mem Params：不碰 Mac 的默认 Params 目录，也不碰 /dev/shm
  mem = Params(str(tmp_path / "params"))
  mem.put("LastGPSPosition", '{"latitude":1.0,"longitude":2.0}', block=True)
  mem.put("MapTargetVelocities", '[{"latitude":1.0,"longitude":2.0,"velocity":10.0}]', block=True)
  mem.put_bool("SmartCruiseControlMap", True, block=True)  # PERSISTENT，不应被误删

  map_params.clear_map_params(mem, ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION)

  assert mem.get("LastGPSPosition") is None
  assert mem.get("MapTargetVelocities") is None
  assert mem.get_bool("SmartCruiseControlMap")


def test_clear_map_params_offroad_removes_targets(tmp_path):
  # OFFROAD 标志下 clear_all 清不到 MapTargetVelocities（它带 ONROAD 标志），完全靠显式 remove
  mem = Params(str(tmp_path / "params"))
  mem.put("LastGPSPosition", '{"latitude":1.0,"longitude":2.0}', block=True)
  mem.put("MapTargetVelocities", "[]", block=True)

  map_params.clear_map_params(mem, ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION)

  assert mem.get("LastGPSPosition") is None
  assert mem.get("MapTargetVelocities") is None


def test_clear_map_params_none_is_noop():
  map_params.clear_map_params(None, ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION)


def test_darwin_never_uses_mem_params(monkeypatch):
  monkeypatch.setattr(map_params.platform, "system", lambda: "Darwin")
  assert map_params.open_mem_params() is None
