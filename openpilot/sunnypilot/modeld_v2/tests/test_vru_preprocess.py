"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.modeld_v2.vru_preprocess import nv12_to_chw_rgb, MODEL_W, MODEL_H


def build_nv12(y_img: np.ndarray, uv_img: np.ndarray, stride: int) -> bytes:
  """y_img (h, w), uv_img (h/2, w) interleaved UV; pads rows to stride with 255."""
  h, w = y_img.shape
  y_rows = np.full((h, stride), 255, dtype=np.uint8)
  y_rows[:, :w] = y_img
  uv_rows = np.full((h // 2, stride), 255, dtype=np.uint8)
  uv_rows[:, :w] = uv_img.ravel().reshape(h // 2, w)
  uv_offset = stride * ((h + 31) // 32 * 32)  # aligned y plane height, >= h
  y_plane = np.full((uv_offset // stride, stride), 255, dtype=np.uint8)
  y_plane[:h] = y_rows
  return y_plane.tobytes() + uv_rows.tobytes()


def uniform_frame(y: int, u: int, v: int, w: int = 64, h: int = 32, stride: int = 64):
  y_img = np.full((h, w), y, dtype=np.uint8)
  uv = np.empty((h // 2, w), dtype=np.uint8)
  uv[:, 0::2] = u
  uv[:, 1::2] = v
  return build_nv12(y_img, uv, stride), w, h, stride, stride * ((h + 31) // 32 * 32)


class TestPreprocess(OpenpilotTestCase):
  def test_shapes(self):
    data, w, h, stride, uv_off = uniform_frame(128, 128, 128)
    out = nv12_to_chw_rgb(data, w, h, stride, uv_off)
    assert out.shape == (3, MODEL_H, MODEL_W)
    assert out.dtype == np.float32 and out.min() >= 0.0 and out.max() <= 1.0

  def test_gray(self):
    data, w, h, stride, uv_off = uniform_frame(128, 128, 128)
    out = nv12_to_chw_rgb(data, w, h, stride, uv_off)
    np.testing.assert_allclose(out, 128.0 / 255.0, atol=2.0 / 255.0)

  def test_black_white(self):
    data, w, h, stride, uv_off = uniform_frame(0, 128, 128)
    np.testing.assert_allclose(nv12_to_chw_rgb(data, w, h, stride, uv_off), 0.0, atol=2.0 / 255.0)
    data, w, h, stride, uv_off = uniform_frame(255, 128, 128)
    np.testing.assert_allclose(nv12_to_chw_rgb(data, w, h, stride, uv_off), 1.0, atol=2.0 / 255.0)

  def test_color_bt601(self):
    # Y=50, U=128, V=255 -> R = 50+1.402*127 = 228, G = 50-0.714*127 = 0 (clip), B = 50
    data, w, h, stride, uv_off = uniform_frame(50, 128, 255)
    out = nv12_to_chw_rgb(data, w, h, stride, uv_off) * 255.0
    np.testing.assert_allclose(out[0], 228.0, atol=2.0)
    np.testing.assert_allclose(out[1], 0.0, atol=2.0)
    np.testing.assert_allclose(out[2], 50.0, atol=2.0)

  def test_stride_padding_ignored(self):
    compact, w, h, s1, o1 = uniform_frame(100, 90, 200, w=64, h=32, stride=64)
    padded, _, _, s2, o2 = uniform_frame(100, 90, 200, w=64, h=32, stride=80)
    a = nv12_to_chw_rgb(compact, w, h, s1, o1)
    b = nv12_to_chw_rgb(padded, w, h, s2, o2)
    np.testing.assert_allclose(a, b, atol=1.0 / 255.0)

  def test_bilinear_identity(self):
    from openpilot.sunnypilot.modeld_v2.vru_preprocess import _bilinear_resize
    img = np.arange(24, dtype=np.float32).reshape(4, 6)
    np.testing.assert_allclose(_bilinear_resize(img, 4, 6), img, atol=1e-6)

  def test_bilinear_linear(self):
    from openpilot.sunnypilot.modeld_v2.vru_preprocess import _bilinear_resize
    img = np.array([[0., 1., 2., 3.]])
    out = _bilinear_resize(img, 1, 2)
    np.testing.assert_allclose(out, [[0.5, 2.5]], atol=1e-6)

  def test_gradient_downscale(self):
    from openpilot.sunnypilot.modeld_v2.vru_preprocess import _bilinear_resize
    img = np.tile(np.arange(64, dtype=np.float32), (8, 1))  # horizontal ramp
    out = _bilinear_resize(img, 8, 32)
    # half-scale sample centers land at 2j+0.5 -> midpoint of ramp values 2j, 2j+1
    np.testing.assert_allclose(out[0], np.arange(32) * 2.0 + 0.5, atol=0.1)
