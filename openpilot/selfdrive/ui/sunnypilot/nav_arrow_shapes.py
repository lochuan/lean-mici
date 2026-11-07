"""导航卡片箭头的几何（无 UI 依赖）。坐标是官方 mici 导航面板的单位坐标：绘制时 ×1.4 + 原点 (31,30)。
直行 / 左右转取自官方复刻；其余形状为本项目新画（右侧版本手写，左侧版本镜像）。
"""
import math
from dataclasses import dataclass

from openpilot.selfdrive.ui.sunnypilot.nav_card import Arrow

Point = tuple[float, float]
Polyline = tuple[Point, ...]
HEAD_LENGTH = 7.0
HEAD_ANGLE = 0.75   # rad


@dataclass(frozen=True)
class ArrowShape:
  polylines: tuple[Polyline, ...] = ()   # 实线：线宽 7、顶点圆点 r3.5
  ghost: tuple[Polyline, ...] = ()       # 淡色（分叉里未选的那条）
  ring: tuple[float, float, float] | None = None   # 环岛环：圆心 x、y、半径
  pin: bool = False                      # 到达图钉


def _head(start: Point, tip: Point, length: float = HEAD_LENGTH) -> Polyline:
  angle = math.atan2(tip[1] - start[1], tip[0] - start[0])
  return ((tip[0] - length * math.cos(angle - HEAD_ANGLE), tip[1] - length * math.sin(angle - HEAD_ANGLE)), tip,
          (tip[0] - length * math.cos(angle + HEAD_ANGLE), tip[1] - length * math.sin(angle + HEAD_ANGLE)))


def _mirror(polylines: tuple[Polyline, ...]) -> tuple[Polyline, ...]:
  return tuple(tuple((-x, y) for x, y in line) for line in polylines)


def _with_head(line: Polyline) -> tuple[Polyline, ...]:
  return (line, _head(line[-2], line[-1]))


_STRAIGHT = ArrowShape(polylines=(((0, 14), (0, -12)), ((-6, -5), (0, -12), (6, -5))))
_RIGHT = ArrowShape(polylines=(((-9, 14), (-9, -4), (-2, -9), (9, -9)), ((3, -15), (9, -9), (3, -3))))
_SLIGHT_RIGHT = ArrowShape(polylines=_with_head(((-6, 14), (-6, 3), (5, -9))))
_SHARP_RIGHT = ArrowShape(polylines=_with_head(((-9, 14), (-9, -8), (7, 5))))
_UTURN_RIGHT = ArrowShape(polylines=_with_head(((-8, 14), (-8, -3), (-5, -9), (0, -11), (5, -9), (8, -3), (8, 8))))
_FORK_RIGHT = ArrowShape(polylines=_with_head(((-1, 14), (-1, 2), (8, -10))), ghost=(((-1, 2), (-1, -12)),))


def _left_of(right: ArrowShape) -> ArrowShape:
  return ArrowShape(polylines=_mirror(right.polylines), ghost=_mirror(right.ghost))


_SHAPES: dict[Arrow, ArrowShape] = {
  Arrow.STRAIGHT: _STRAIGHT,
  Arrow.RIGHT: _RIGHT, Arrow.LEFT: _left_of(_RIGHT),
  Arrow.SLIGHT_RIGHT: _SLIGHT_RIGHT, Arrow.SLIGHT_LEFT: _left_of(_SLIGHT_RIGHT),
  Arrow.SHARP_RIGHT: _SHARP_RIGHT, Arrow.SHARP_LEFT: _left_of(_SHARP_RIGHT),
  Arrow.UTURN_RIGHT: _UTURN_RIGHT, Arrow.UTURN_LEFT: _left_of(_UTURN_RIGHT),
  Arrow.FORK_RIGHT: _FORK_RIGHT, Arrow.FORK_LEFT: _left_of(_FORK_RIGHT),
  Arrow.ROUNDABOUT: ArrowShape(polylines=(((0, 14), (0, 7)), ((7, -8), (12, -14)), _head((7, -8), (12, -14), 6)), ring=(0, -1, 9)),
  Arrow.ARRIVE: ArrowShape(pin=True),
}


def arrow_shape(arrow: Arrow) -> ArrowShape:
  return _SHAPES[arrow]
