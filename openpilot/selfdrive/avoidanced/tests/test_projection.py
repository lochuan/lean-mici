"""Tests for camera -> vehicle-frame projection (synthetic intrinsics, no real camera)."""

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.projection import (project_box_to_vehicle, project_detections,
                                                       roi_meta_for, roi_to_full)

# Synthetic intrinsics from the brief: fx=fy=1000, cx=320, cy=192, camera 1.2 m up, pitch 0.
FX = FY = 1000.0
CX, CY = 320.0, 192.0
HEIGHT = 1.2


def _project(u, v, **kwargs):
  kwargs.setdefault("fx", FX)
  kwargs.setdefault("fy", FY)
  kwargs.setdefault("cx", CX)
  kwargs.setdefault("cy", CY)
  kwargs.setdefault("height", HEIGHT)
  kwargs.setdefault("pitch", 0.0)
  return project_box_to_vehicle(u=u, v=v, **kwargs)


# --- brief Step 1 (verbatim) ---------------------------------------------------

def test_projection_bottom_center_to_ground():
  # Pixel (320, 200): (v-cy)/fy = 0.008 below the horizon -> ground at 1.2/0.008 = 150 m.
  out = project_box_to_vehicle(u=320, v=200, fx=1000, fy=1000, cx=320, cy=192, height=1.2, pitch=0.0)
  assert out is not None
  assert abs(out["dRel"] - 150.0) < 0.01


# --- closed-form geometry ------------------------------------------------------

def test_projection_lateral_sign_and_scale():
  # x_n = (u-cx)/fx = +-0.1 -> yRel = -+0.1 * 150 (right negative, left positive).
  right = _project(u=420, v=200)
  left = _project(u=220, v=200)
  assert right["yRel"] == pytest.approx(-15.0, abs=0.01)
  assert left["yRel"] == pytest.approx(15.0, abs=0.01)
  assert right["dRel"] == pytest.approx(left["dRel"], abs=0.01)


def test_projection_pitch_closed_form():
  # Optical-axis pixel with the camera pitched down hits the ground at height/tan(pitch).
  pitch = 0.1
  out = _project(u=320, v=192, pitch=pitch)
  assert out["dRel"] == pytest.approx(HEIGHT / np.tan(pitch), abs=0.01)
  assert out["yRel"] == pytest.approx(0.0, abs=1e-9)


def test_projection_above_horizon_returns_none():
  # A ray pointing up never reaches the ground plane.
  assert _project(u=320, v=10) is None


def test_projection_camera_to_front_aligns_to_radar_origin():
  # Radar dRel originates at the front bumper; the windshield camera sits behind
  # it, so the camera-frame distance is larger and the offset is subtracted.
  out = _project(u=320, v=200, camera_to_front=C.CAMERA_TO_FRONT)
  assert out["dRel"] == pytest.approx(150.0 - C.CAMERA_TO_FRONT, abs=0.01)


def test_projection_yaw_rotates_ground_point():
  # Camera yawed left: a point straight ahead of the camera moves left in the car frame.
  out = _project(u=320, v=200, yaw=0.05)
  assert out["yRel"] > 0.0
  assert out["dRel"] == pytest.approx(150.0 * np.cos(0.05), abs=0.01)


# --- ROI <-> full-frame mapping ------------------------------------------------

def test_roi_meta_full_frame_when_wide():
  # 1344 * 384/640 = 806 > 760: the whole frame is the ROI (mici wide cam).
  meta = roi_meta_for(1344, 760)
  assert meta.offset_v == 0.0
  assert meta.scale_u == pytest.approx(1344 / 640)
  assert meta.scale_v == pytest.approx(760 / 384)


def test_roi_meta_bottom_crop_when_tall():
  meta = roi_meta_for(1928, 1208)
  crop_h = round(1928 * 384 / 640)
  assert meta.offset_v == 1208 - crop_h
  assert meta.scale_v == pytest.approx(crop_h / 384)
  assert meta.scale_u == pytest.approx(1928 / 640)


def test_roi_to_full_roundtrip():
  meta = roi_meta_for(1928, 1208)
  assert roi_to_full(640, 384, meta) == (1928.0, 1208.0)
  assert roi_to_full(0, 0, meta) == (0.0, 1208 - round(1928 * 384 / 640))


# --- detection list projection --------------------------------------------------

def _det(x1, y1, x2, y2, cls="car", conf=0.9):
  return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cls": cls, "conf": conf}


def _project_dets(dets, meta=None, **kwargs):
  kwargs.setdefault("fx", 425.25)
  kwargs.setdefault("fy", 425.25)
  kwargs.setdefault("cx", 672.0)
  kwargs.setdefault("cy", 380.0)
  kwargs.setdefault("height", 1.2)
  kwargs.setdefault("pitch", 0.0)
  kwargs.setdefault("camera_to_front", 0.0)  # pure geometry here; the daemon passes C.CAMERA_TO_FRONT
  kwargs.setdefault("roi_meta", meta)
  return project_detections(dets, **kwargs)


def test_project_detections_schema_and_weights():
  meta = roi_meta_for(1344, 760)
  dets = [_det(300, 300, 340, 360, cls="person", conf=0.8),
          _det(100, 180, 140, 200, cls="car", conf=0.7)]
  out = _project_dets(dets, meta=meta)
  assert len(out) == 2
  assert set(out[0]) == {"dRel", "yRel", "cls", "conf", "w"}
  assert out[0]["w"] == C.VRU_WEIGHT
  assert out[0]["cls"] == "person" and out[0]["conf"] == 0.8
  assert out[1]["w"] == C.VEHICLE_WEIGHT


def test_project_detections_uses_full_frame_pixels():
  # Bottom-centre (320, 360) in ROI coords must be inverse-mapped to full-frame
  # (672, 712.5) before projecting: d = 1.2 / ((712.5-380)/425.25).
  meta = roi_meta_for(1344, 760)
  out = _project_dets([_det(300, 300, 340, 360)], meta=meta)
  expected_d = 1.2 / ((360 * 760 / 384 - 380.0) / 425.25)
  assert out[0]["dRel"] == pytest.approx(expected_d, abs=0.01)
  assert out[0]["yRel"] == pytest.approx(0.0, abs=1e-9)


def test_project_detections_skips_sky_boxes():
  # Bottom edge above the horizon line: the ray never reaches the ground.
  meta = roi_meta_for(1344, 760)
  assert _project_dets([_det(300, 0, 340, 50)], meta=meta) == []


def test_project_detections_empty():
  assert _project_dets([]) == []
  assert _project_dets(None) == []
