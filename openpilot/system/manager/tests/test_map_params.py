"""S1b：manager 上路/下路切换时清 mem Params（/dev/shm/params）里的 SCC-map 键。"""
from openpilot.common.params import Params, ParamKeyFlag
from openpilot.system.manager import map_params


def test_clear_map_params_removes_targets(tmp_path):
  # 临时目录当 mem Params：不碰 Mac 的默认 Params 目录，也不碰 /dev/shm
  mem = Params(str(tmp_path / "params"))
  mem.put("MapTargetVelocities", '{"sample_time_ns":1,"valid_until_ns":2,"points":[{"latitude":1,"longitude":2,"distance_m":40,"velocity":10}]}',
          block=True)
  mem.put_bool("SmartCruiseControlMap", True, block=True)  # PERSISTENT，不应被误删

  map_params.clear_map_params(mem, ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION)

  assert mem.get("MapTargetVelocities") is None
  assert mem.get_bool("SmartCruiseControlMap")


def test_clear_map_params_offroad_removes_targets(tmp_path):
  # OFFROAD 标志下 clear_all 清不到 MapTargetVelocities（它带 ONROAD 标志），完全靠显式 remove
  mem = Params(str(tmp_path / "params"))
  mem.put("MapTargetVelocities", "[]", block=True)

  map_params.clear_map_params(mem, ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION)

  assert mem.get("MapTargetVelocities") is None


def test_clear_map_params_none_is_noop():
  map_params.clear_map_params(None, ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION)


def test_darwin_never_uses_mem_params(monkeypatch):
  monkeypatch.setattr(map_params.platform, "system", lambda: "Darwin")
  assert map_params.open_mem_params() is None


def _mem_with_targets(tmp_path):
  mem = Params(str(tmp_path / "params"))
  mem.put("MapTargetVelocities", '{"sample_time_ns":1,"valid_until_ns":2,"points":[{"latitude":1,"longitude":2,"distance_m":40,"velocity":10}]}',
          block=True)
  mem.put_bool("SmartCruiseControlMap", True, block=True)
  return mem


def test_writer_gone_onroad_removes_only_map_keys(tmp_path):
  # AU-5：上路期间 bigmodeld 不在运行 → 删掉它留下的目标；其他键不动，也不 clear_all
  mem = _mem_with_targets(tmp_path)
  map_params.remove_map_targets_if_writer_gone(mem, started=True, writer_running=False)
  assert mem.get("MapTargetVelocities") is None
  assert mem.get_bool("SmartCruiseControlMap")


def test_writer_running_or_offroad_keeps_keys(tmp_path):
  mem = _mem_with_targets(tmp_path)
  map_params.remove_map_targets_if_writer_gone(mem, started=True, writer_running=True)
  map_params.remove_map_targets_if_writer_gone(mem, started=False, writer_running=False)
  assert mem.get("MapTargetVelocities") is not None


def test_writer_gone_with_no_mem_params_is_noop():
  map_params.remove_map_targets_if_writer_gone(None, started=True, writer_running=False)
