"""箭头几何（单位坐标，×1.4 + 原点 (31,30) 在绘制处套用，与官方复刻一致）。
  pytest openpilot/selfdrive/ui/tests/test_nav_arrow_shapes.py
"""
import pytest

from openpilot.selfdrive.ui.sunnypilot.nav_arrow_shapes import arrow_shape
from openpilot.selfdrive.ui.sunnypilot.nav_card import Arrow


def mirrored(polylines):
  return tuple(tuple((-x, y) for x, y in line) for line in polylines)


@pytest.mark.parametrize("arrow", list(Arrow))
def test_every_arrow_has_a_drawable_shape(arrow):
  shape = arrow_shape(arrow)
  assert shape.polylines or shape.pin or shape.ring


@pytest.mark.parametrize("left, right", [
  (Arrow.LEFT, Arrow.RIGHT), (Arrow.SLIGHT_LEFT, Arrow.SLIGHT_RIGHT), (Arrow.SHARP_LEFT, Arrow.SHARP_RIGHT),
  (Arrow.UTURN_LEFT, Arrow.UTURN_RIGHT), (Arrow.FORK_LEFT, Arrow.FORK_RIGHT),
])
def test_left_variants_mirror_right_variants(left, right):
  assert arrow_shape(left).polylines == mirrored(arrow_shape(right).polylines)
  assert arrow_shape(left).ghost == mirrored(arrow_shape(right).ghost)


def test_official_straight_and_right_turn_geometry():
  assert arrow_shape(Arrow.STRAIGHT).polylines == (((0, 14), (0, -12)), ((-6, -5), (0, -12), (6, -5)))
  assert arrow_shape(Arrow.RIGHT).polylines == (((-9, 14), (-9, -4), (-2, -9), (9, -9)), ((3, -15), (9, -9), (3, -3)))


def test_only_roundabout_has_ring_and_only_arrive_has_pin():
  assert [a for a in Arrow if arrow_shape(a).ring] == [Arrow.ROUNDABOUT]
  assert [a for a in Arrow if arrow_shape(a).pin] == [Arrow.ARRIVE]
