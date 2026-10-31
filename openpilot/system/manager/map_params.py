"""SCC-map 的 mem Params（/dev/shm/params）清理。

manager 的 CLEAR_ON_ONROAD_TRANSITION / CLEAR_ON_OFFROAD_TRANSITION 只清持久目录（Params()），
不会碰 /dev/shm/params；bigmodeld 写入的弯道目标因此会在上路/下路切换时残留。
本模块在切换点对 mem Params 做同样的清理，并显式删掉 MapTargetVelocities。

Darwin 没有 /dev/shm，调用方用 open_mem_params() 取 None，绝不能误清 Mac 的持久目录。
"""
import platform

from openpilot.common.params import Params

MAP_TARGET_KEYS = ("MapTargetVelocities",)


def open_mem_params() -> Params | None:
  if platform.system() == "Darwin":
    return None
  return Params("/dev/shm/params")


def remove_map_targets(mem_params: Params | None) -> None:
  if mem_params is None:
    return
  for key in MAP_TARGET_KEYS:
    mem_params.remove(key)


def clear_map_params(mem_params: Params | None, flag: int) -> None:
  if mem_params is None:
    return
  mem_params.clear_all(flag)
  remove_map_targets(mem_params)


def remove_map_targets_if_writer_gone(mem_params: Params | None, started: bool, writer_running: bool) -> None:
  """上路期间 bigmodeld 不在运行（关了开关、崩溃待重启、退出卡住）时，不让它最后写的目标留给下路。

  只删目标键，不 clear_all；manager 每拍（约 2 Hz）调用，键不存在时是空操作。
  """
  if started and not writer_running:
    remove_map_targets(mem_params)
