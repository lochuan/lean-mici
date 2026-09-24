"""Tests for the wide-road camera ROI pipeline (no camerad required)."""

import numpy as np
import pytest
from openpilot.cereal.visionipc import VisionStreamType

from openpilot.selfdrive.eagled import camera_stream as cs
from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.camera_stream import CameraStream, resize_bilinear
from openpilot.selfdrive.eagled.projection import roi_meta_for


# --- bilinear resize ------------------------------------------------------------

def test_resize_bilinear_identity():
  img = np.arange(12, dtype=np.uint8).reshape(3, 4, 1)
  assert np.array_equal(resize_bilinear(img, 4, 3), img)


def test_resize_bilinear_downscale_values():
  # 2x2 -> 1x1, centre-aligned: the mean of the four pixels.
  img = np.array([[[0], [0]], [[255], [255]]], dtype=np.uint8)
  out = resize_bilinear(img, 1, 1)
  assert out.shape == (1, 1, 1)
  assert out[0, 0, 0] in (127, 128)


def test_resize_bilinear_preserves_shape_and_dtype():
  img = np.zeros((760, 1344, 3), dtype=np.uint8)
  out = resize_bilinear(img, 640, 384)
  assert out.shape == (384, 640, 3)
  assert out.dtype == np.uint8


# --- ROI crop + meta ------------------------------------------------------------

def test_roi_from_rgb_matches_meta():
  rgb = np.zeros((760, 1344, 3), dtype=np.uint8)
  roi, meta = cs.roi_from_rgb(rgb)
  assert roi.shape == (384, 640, 3)
  assert roi.dtype == np.uint8
  assert meta == roi_meta_for(1344, 760)


def test_roi_from_rgb_bottom_crop():
  # Marker bands: anything above the bottom crop must vanish, the bottom must stay.
  rgb = np.zeros((1208, 1928, 3), dtype=np.uint8)
  rgb[:20, :, :] = 255            # above the bottom crop -> must not appear
  rgb[-8:, :, :] = 255            # bottom band -> ROI bottom
  roi, meta = cs.roi_from_rgb(rgb)
  assert meta.offset_v == pytest.approx(1208 - round(1928 * 384 / 640))
  assert roi[-1].mean() == 255    # bottom band kept
  assert roi.mean() < 10          # everything else (incl. the top band) cropped out


def test_roi_from_rgb_native_mode_does_not_resample():
  rgb = np.random.randint(0, 255, (760, 1344, 3), dtype=np.uint8)
  roi, meta = cs.roi_from_rgb(rgb, mode=C.ROI_MODE_NATIVE, horizon_row=380.0)
  assert roi.shape == (384, 640, 3)
  assert meta.scale_u == 1.0 and meta.scale_v == 1.0
  # 原生模式必须是纯切片,像素逐字节相同(不经插值)
  top, left = int(meta.offset_v), int(meta.offset_u)
  assert np.array_equal(roi, rgb[top:top + 384, left:left + 640])


# --- intrinsics ------------------------------------------------------------------

def test_scaled_intrinsics_match_native_resolution():
  # comma 4 (mici) wide road cam: 425.25 px focal at native 1344x760.
  fx, fy, cx, cy = cs.scaled_intrinsics(1344, 760)
  assert fx == pytest.approx(425.25)
  assert fy == pytest.approx(425.25)
  assert (cx, cy) == (672.0, 380.0)


def test_scaled_intrinsics_scale_with_resolution():
  fx, fy, cx, cy = cs.scaled_intrinsics(2688, 1520)
  assert fx == pytest.approx(850.5)
  assert fy == pytest.approx(850.5)
  assert (cx, cy) == (1344.0, 760.0)


# --- connect / frame behaviour on a machine without camerad ----------------------

def test_camera_stream_frame_returns_none_without_camerad():
  stream = CameraStream()
  assert stream.frame() is None
  assert stream.intrinsics is None


def test_camera_stream_connect_throttles_retries():
  now = [100.0]
  stream = CameraStream(clock=lambda: now[0])
  assert stream.connect() is False            # no camerad on the test machine
  first = stream.last_connect_t
  now[0] = 100.5
  assert stream.connect() is False            # inside the retry window: skipped
  assert stream.last_connect_t == first
  now[0] = 101.5
  stream.connect()
  assert stream.last_connect_t == 101.5       # window elapsed: tried again


# --- connect() success path: intrinsics + frame_size together ---------------------

class _FakeIpcClient:
  """Stands in for VisionIpcClient so connect() runs its real success path."""

  streams = [VisionStreamType.VISION_STREAM_WIDE_ROAD]

  def __init__(self, name, stream, conflate):
    self.width, self.height = 1344, 760

  def connect(self, block):
    return True

  def is_connected(self):
    return True

  @classmethod
  def available_streams(cls, name, block=False):
    return cls.streams


def test_camera_stream_connect_sets_frame_size_and_intrinsics(monkeypatch):
  # frame_height for truncated-box rejection comes from this attribute; if
  # connect() stops setting it the daemon silently loses the rejection.
  monkeypatch.setattr(cs, "VisionIpcClient", _FakeIpcClient)
  stream = CameraStream(clock=lambda: 100.0)
  assert stream.connect() is True
  assert stream.frame_size == (1344, 760)
  assert stream.intrinsics == cs.scaled_intrinsics(1344, 760)
