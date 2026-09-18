"""Tests for the wide-road camera ROI pipeline (no camerad required)."""

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import camera_stream as cs
from openpilot.selfdrive.avoidanced.camera_stream import CameraStream, resize_bilinear
from openpilot.selfdrive.avoidanced.projection import roi_meta_for


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
