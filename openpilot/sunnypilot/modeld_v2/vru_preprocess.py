"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

MODEL_W = 320
MODEL_H = 192

# BT.601 full range (JPEG) YUV -> RGB
_R_V = 1.402
_GU_U = 0.344136
_GV_V = 0.714136
_B_U = 1.772


def _bilinear_resize(img: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
  h, w = img.shape
  ys = np.clip((np.arange(out_h) + 0.5) * (h / out_h) - 0.5, 0, h - 1)
  xs = np.clip((np.arange(out_w) + 0.5) * (w / out_w) - 0.5, 0, w - 1)
  y0 = np.floor(ys).astype(np.intp)
  y1 = np.minimum(y0 + 1, h - 1)
  x0 = np.floor(xs).astype(np.intp)
  x1 = np.minimum(x0 + 1, w - 1)
  wy = (ys - y0).astype(np.float32)[:, None]
  wx = (xs - x0).astype(np.float32)[None, :]
  img = img.astype(np.float32)
  top = img[np.ix_(y0, x0)] * (1.0 - wx) + img[np.ix_(y0, x1)] * wx
  bot = img[np.ix_(y1, x0)] * (1.0 - wx) + img[np.ix_(y1, x1)] * wx
  return top * (1.0 - wy) + bot * wy


def nv12_to_chw_rgb(data, width: int, height: int, stride: int, uv_offset: int,
                    out_w: int = MODEL_W, out_h: int = MODEL_H) -> np.ndarray:
  assert width % 2 == 0 and height % 2 == 0, "even dimensions required"
  buf = np.frombuffer(data, dtype=np.uint8)
  y_plane = buf[:uv_offset].reshape(-1, stride)[:height, :width].astype(np.float32)
  uv_plane = buf[uv_offset:].reshape(-1, stride)[:height // 2, :width]

  y = _bilinear_resize(y_plane, out_h, out_w)
  u = _bilinear_resize(uv_plane[:, 0::2], out_h // 2, out_w // 2)
  v = _bilinear_resize(uv_plane[:, 1::2], out_h // 2, out_w // 2)
  u = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1) - 128.0
  v = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1) - 128.0

  r = y + _R_V * v
  g = y - _GU_U * u - _GV_V * v
  b = y + _B_U * u
  rgb = np.stack([np.clip(r, 0, 255), np.clip(g, 0, 255), np.clip(b, 0, 255)], axis=0)
  return (rgb / 255.0).astype(np.float32)
