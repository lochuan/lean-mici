"""模型几何的唯一解释权：相机系 → 车体系换算（spec #1 / 票 #2）。

坐标系（见 CONTEXT.md 词条）：
- 相机系（modelV2 原生）：x 以挡风玻璃后方相机为原点、向前为正，y 右正。
- 车体系（规范）：x 以前保险杠为原点（dRel 语义），y 左正（yRel 语义）。
- 安装偏移 camera_to_front：相机在前保险杠后方多远。相机系地面点比车体系
  远，故车体系 x = 相机系 x - camera_to_front（与 projection.py 的
  ``dRel = x_v - camera_to_front`` 同一语义）。

消费方（eagled 判定、lanlinkd 展示、车内 UI）一律经本模块取几何，
不在各自路径里手写换算——坐标语义只在这一处。
"""
from __future__ import annotations

from dataclasses import dataclass

CAMERA_TO_FRONT_DEFAULT = 1.5  # 出厂默认安装偏移：相机在前保险杠后方 1.5m


def read_camera_to_front(params) -> float:
  """安装偏移的唯一读点（票 #6）：Params 键 ``CameraToFront``，未落盘回退出厂默认。

  消费方（eagled 判定、lanlinkd 展示、车内 UI）每帧经本函数取值——保存新值
  下一帧即生效，任何地方不得再直读常量或另设读点。params duck-typed（只要有
  ``get(key)``，返回 str/bytes/float/None 皆可）。
  """
  try:
    v = params.get("CameraToFront")
  except Exception:
    v = None  # 旧库未注册该键等异常形态：回退出厂默认（同 apply_param_overrides 惯例）
  return CAMERA_TO_FRONT_DEFAULT if v is None else float(v)


@dataclass(frozen=True)
class VehicleFrameLine:
  """车体系折线。x 前正（原点前保险杠），y 左正，z 垂直向，逐点对应。"""
  x: tuple[float, ...]
  y: tuple[float, ...]
  z: tuple[float, ...]


@dataclass(frozen=True)
class VehicleFrameGeometry:
  """一帧模型几何的车体系形态。

  lane_lines 按 modelV2 惯例 4 条（0=远左外线 1=本道左 2=本道右 3=远右外线），
  road_edges 2 条，path 为模型预测路径。槽位固定、索引安全：缺字段/空列表/
  序列不一致的项为 None，绝不抛异常。
  """
  lane_lines: tuple[VehicleFrameLine | None, ...]
  road_edges: tuple[VehicleFrameLine | None, ...]
  path: VehicleFrameLine | None


def line_to_vehicle_frame(x, y, z, camera_to_front: float) -> VehicleFrameLine | None:
  """一条相机系折线 → 车体系折线。

  x/y/z 为逐点序列（duck-typed：list/tuple/capnp 列表皆可）。缺任一序列、
  序列为空或长度不一致返回 None，不抛异常——模型桩形态千差万别。
  z 无值（None 或空，capnp 未填即空列表）按地面处理；有值则须与 x 等长。
  """
  if x is None or y is None:
    return None
  xs, ys = list(x), list(y)
  if len(xs) == 0 or len(xs) != len(ys):
    return None
  zs = [] if z is None else list(z)
  if len(zs) == 0:
    zs = [0.0] * len(xs)
  if len(zs) != len(xs):
    return None
  return VehicleFrameLine(
    x=tuple(float(v) - camera_to_front for v in xs),
    y=tuple(-float(v) for v in ys),
    z=tuple(float(v) for v in zs),
  )


def vehicle_to_camera_frame(x, y, z, camera_to_front: float) -> tuple:
  """车体系点 → 相机系点（``line_to_vehicle_frame`` 的逐点逆）。

  投影边界用：往相机透视矩阵（输入原点=相机）送点前补回安装偏移。
  x/y/z 可为标量或 numpy 数组（逐点广播）；返回 (x + 偏移, -y, z) 的元组。
  """
  return (x + camera_to_front, -y, z)


def geometry_to_vehicle_frame(model_v2, camera_to_front: float) -> VehicleFrameGeometry:
  """一帧模型几何（duck-typed modelV2）→ 车体系几何。

  取 laneLines / roadEdges / position（模型预测路径）。任何缺字段、空列表、
  逐点序列不一致都降级为 None 项或空类别，不抛异常。
  """
  def _line_at(seq, idx: int) -> VehicleFrameLine | None:
    if seq is None or len(seq) <= idx:
      return None
    item = seq[idx]
    return line_to_vehicle_frame(
      getattr(item, "x", None), getattr(item, "y", None), getattr(item, "z", None), camera_to_front)

  lines = getattr(model_v2, "laneLines", None)
  edges = getattr(model_v2, "roadEdges", None)
  position = getattr(model_v2, "position", None)
  path = None
  if position is not None:
    path = line_to_vehicle_frame(
      getattr(position, "x", None), getattr(position, "y", None), getattr(position, "z", None), camera_to_front)
  return VehicleFrameGeometry(
    lane_lines=tuple(_line_at(lines, i) for i in range(4)),
    road_edges=tuple(_line_at(edges, i) for i in range(2)),
    path=path,
  )
