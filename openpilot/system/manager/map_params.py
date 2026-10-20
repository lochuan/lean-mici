"""SCC-map 的 mem Params（/dev/shm/params）清理。

manager 的 CLEAR_ON_ONROAD_TRANSITION / CLEAR_ON_OFFROAD_TRANSITION 只清持久目录（Params()），
不会碰 /dev/shm/params；bigmodeld 写入的弯道目标与位置因此会在上路/下路切换时残留。
本模块在切换点对 mem Params 做同样的清理，并显式删掉 LastGPSPosition（PERSISTENT，
clear_all 清不到）与 MapTargetVelocities。

Darwin 没有 /dev/shm，调用方用 open_mem_params() 取 None，绝不能误清 Mac 的持久目录。
"""
import platform

from openpilot.common.params import Params

MAP_TARGET_KEYS = ("LastGPSPosition", "MapTargetVelocities")


def open_mem_params() -> Params | None:
  if platform.system() == "Darwin":
    return None
  return Params("/dev/shm/params")


def clear_map_params(mem_params: Params | None, flag: int) -> None:
  if mem_params is None:
    return
  mem_params.clear_all(flag)
  for key in MAP_TARGET_KEYS:
    mem_params.remove(key)
