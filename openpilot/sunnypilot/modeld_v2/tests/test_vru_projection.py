"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.modeld_v2.vru_projection import GroundProjector

F, CX, CY, HEIGHT = 1000.0, 964.0, 604.0, 1.2
K = np.array([[F, 0.0, CX], [0.0, F, CY], [0.0, 0.0, 1.0]])
Z3 = np.zeros(3)


def projector(rpy=Z3, wfe=Z3, height=HEIGHT):
  return GroundProjector(K, rpy, wfe, height)


class TestProjection(OpenpilotTestCase):
  def test_center_forward(self):
    p = projector()
    # ground point 12 m straight ahead: p_calib=[12, 0, 1.2] -> view [0, 1.2, 12]
    np.testing.assert_allclose(p.ground_to_pixel(12.0, 0.0), (964.0, 704.0), atol=1e-6)
    np.testing.assert_allclose(p.pixel_to_ground(964.0, 704.0), (12.0, 0.0), atol=1e-6)

  def test_right_is_negative_y(self):
    p = projector()
    # y_left = -2 (right of center): p_calib=[12, 2, 1.2] -> u = 964 + 2*1000/12
    u, v = p.ground_to_pixel(12.0, -2.0)
    np.testing.assert_allclose([u, v], [964.0 + 2000.0 / 12.0, 704.0], atol=1e-6)
    x, y = p.pixel_to_ground(u, v)
    np.testing.assert_allclose([x, y], [12.0, -2.0], atol=1e-6)

  def test_sky_and_horizon(self):
    p = projector()
    assert p.pixel_to_ground(964.0, 500.0) is None   # above center
    assert p.pixel_to_ground(964.0, 604.0) is None   # exactly horizon (ray_z = 0)

  def test_roundtrip_with_rotation(self):
    p = projector(rpy=np.array([0.02, -0.05, 0.01]), wfe=np.array([0.0, 0.1, -0.02]))
    for x, y in ((5.0, 1.5), (12.0, -2.0), (30.0, 0.5), (8.0, -4.0)):
      u, v = p.ground_to_pixel(x, y)
      assert 0 <= u < 1928 and 0 <= v < 1208
      rx, ry = p.pixel_to_ground(u, v)
      np.testing.assert_allclose([rx, ry], [x, y], atol=1e-6)

  def test_monotonic(self):
    p = projector()
    xs = [p.pixel_to_ground(964.0, v)[0] for v in (620, 700, 800, 1000)]
    assert all(a > b for a, b in zip(xs, xs[1:], strict=False))  # lower pixel -> closer
    ys = [p.pixel_to_ground(u, 800.0)[1] for u in (700, 964, 1200)]
    assert all(a > b for a, b in zip(ys, ys[1:], strict=False))  # righter pixel -> more negative y
