"""Tests for camera -> vehicle-frame projection (synthetic intrinsics, no real camera)."""

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.projection import (project_box_to_vehicle, project_detections,
                                                       roi_meta_for, roi_to_full)

# Synthetic intrinsics from the brief: fx=fy=1000, cx=320, cy=192, camera 1.2 m up, pitch 0.
# Suffixed _0 because the native-ROI tests below bind FX/FY/CX/CY to the mici
# reference intrinsics; a module-level rebinding would silently change _project.
FX0 = FY0 = 1000.0
CX0, CY0 = 320.0, 192.0
HEIGHT = 1.2


def _project(u, v, **kwargs):
  kwargs.setdefault("fx", FX0)
  kwargs.setdefault("fy", FY0)
  kwargs.setdefault("cx", CX0)
  kwargs.setdefault("cy", CY0)
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


# --- ROI modes: native 1:1 window + RoiMeta.offset_u (task 1 brief) -------------

import math
from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.projection import (RoiMeta, project_box_to_vehicle,
                                                       roi_meta_for, roi_to_full)

W, H, FX, FY, CX, CY = 1344.0, 760.0, 425.25, 425.25, 672.0, 380.0


def test_offset_u_is_appended_so_positional_construction_still_works():
  """RoiMeta 在 test_shadow / test_daemon_fusion 里是位置参数构造的。"""
  m = RoiMeta(1.0, 2.0, 3.0)
  assert (m.scale_u, m.scale_v, m.offset_v, m.offset_u) == (1.0, 2.0, 3.0, 0.0)


def test_native_mode_is_one_to_one_and_centred():
  m = roi_meta_for(W, H, mode=C.ROI_MODE_NATIVE, horizon_row=CY)
  assert m.scale_u == 1.0
  assert m.scale_v == 1.0
  assert m.offset_u == (W - 640) / 2.0            # 居中于 cx
  assert m.offset_v == CY - C.ROI_HORIZON_MARGIN  # 顶边在地平线上方 margin 行


def test_squash_mode_is_unchanged():
  m = roi_meta_for(W, H, mode=C.ROI_MODE_SQUASH)
  crop_h = min(H, round(W * 384 / 640))
  assert m.scale_u == W / 640
  assert m.scale_v == crop_h / 384
  assert m.offset_v == H - crop_h
  assert m.offset_u == 0.0


def _forward_project(d, y, height, pitch, roll=0.0):
  """车体坐标 (d, y, 地面) -> 全帧像素 (u, v)。project_box_to_vehicle 的解析逆。"""
  cp, sp = math.cos(pitch), math.sin(pitch)
  y_n = (height * cp - d * sp) / (d * cp + height * sp)
  t = height / (y_n * cp + sp)
  x_n = -y / t
  # project_box_to_vehicle 反投影前把像素归一化坐标旋转 -roll(绕光轴),
  # 正向投影必须施加其逆旋转 (+roll) 才能闭环。
  if roll:
    cr, sr = math.cos(roll), math.sin(roll)
    x_n, y_n = x_n * cr - y_n * sr, x_n * sr + y_n * cr
  return x_n * FX + CX, y_n * FY + CY


def test_geometry_closes_the_loop_in_both_roi_modes():
  """给定车体坐标 -> 正向算像素 -> 映射进 ROI -> 反投影, 闭环误差应 < 1cm。

  这条能同时抓住 offset_u 缺失、ROI 模式与投影不一致、缩放方向写反。
  """
  for mode in (C.ROI_MODE_SQUASH, C.ROI_MODE_NATIVE):
    meta = roi_meta_for(W, H, mode=mode, horizon_row=CY)
    for d, y in [(10.0, 0.0), (20.0, 1.5), (40.0, -2.0), (5.0, 2.0)]:
      u_full, v_full = _forward_project(d, y, C.CAMERA_HEIGHT, 0.0)
      u_roi = (u_full - meta.offset_u) / meta.scale_u
      v_roi = (v_full - meta.offset_v) / meta.scale_v
      u_back, v_back = roi_to_full(u_roi, v_roi, meta)
      assert abs(u_back - u_full) < 1e-6 and abs(v_back - v_full) < 1e-6
      pt = project_box_to_vehicle(u=u_back, v=v_back, fx=FX, fy=FY, cx=CX, cy=CY,
                                  height=C.CAMERA_HEIGHT, pitch=0.0, yaw=0.0,
                                  camera_to_front=0.0)
      assert pt is not None
      assert abs(pt["dRel"] - d) < 0.01, f"{mode} d={d}"
      assert abs(pt["yRel"] - y) < 0.01, f"{mode} y={y}"


# --- live extrinsics calibration (task 2 brief) ---------------------------------

from openpilot.selfdrive.avoidanced.projection import (CalibratedGeometry,
                                                       geometry_from_calibration,
                                                       horizon_row_for)


class _FakeCal:
  def __init__(self, status, rpy):
    self.calStatus = status
    self.rpyCalib = rpy


def test_uncalibrated_geometry_is_invalid():
  g = geometry_from_calibration(_FakeCal("uncalibrated", []), valid=True)
  assert not g.valid


def test_calibrated_geometry_carries_rpy():
  g = geometry_from_calibration(_FakeCal("calibrated", [0.01, 0.02, 0.03]), valid=True)
  assert g.valid
  assert (g.roll, g.pitch, g.yaw) == (0.01, 0.02, 0.03)


def test_stale_message_is_invalid_even_if_calibrated():
  g = geometry_from_calibration(_FakeCal("calibrated", [0.0, 0.0, 0.0]), valid=False)
  assert not g.valid


def test_short_rpy_is_invalid():
  g = geometry_from_calibration(_FakeCal("calibrated", [0.0, 0.0]), valid=True)
  assert not g.valid


def test_horizon_row_tracks_pitch():
  flat = horizon_row_for(CY, FY, CalibratedGeometry(True, 0.0, 0.0, 0.0))
  assert flat == CY
  # rpyCalib pitch 为正 = 相机下俯(见 calibrationd.py 的 observed_rpy 拟合);
  # 相机下俯时地平线在图像中上移,所以正 pitch 的地平线行必须小于光心行。
  pitched_down = horizon_row_for(CY, FY, CalibratedGeometry(True, 0.0, 0.01, 0.0))
  assert pitched_down < CY
  invalid = horizon_row_for(CY, FY, CalibratedGeometry(False, 0.0, 0.5, 0.0))
  assert invalid == CY           # 未标定时忽略 pitch


def test_projection_closes_the_loop_with_live_pitch():
  pitch = 0.012
  meta = roi_meta_for(W, H, mode=C.ROI_MODE_NATIVE,
                      horizon_row=horizon_row_for(CY, FY, CalibratedGeometry(True, 0.0, pitch, 0.0)))
  for d, y in [(15.0, 1.0), (35.0, -1.5)]:
    u_full, v_full = _forward_project(d, y, C.CAMERA_HEIGHT, pitch)
    pt = project_box_to_vehicle(u=u_full, v=v_full, fx=FX, fy=FY, cx=CX, cy=CY,
                                height=C.CAMERA_HEIGHT, pitch=pitch, yaw=0.0,
                                roll=0.0, camera_to_front=0.0)
    assert pt is not None
    assert abs(pt["dRel"] - d) < 0.01 and abs(pt["yRel"] - y) < 0.01
  # 正 pitch(相机下俯)让地平线上移,NATIVE ROI 顶边跟着上移(行号变小)。
  assert meta.offset_v < CY - C.ROI_HORIZON_MARGIN


def test_projection_closes_the_loop_with_live_roll():
  """roll != 0 闭环:正向投影施加 +roll 旋转,反投影应恢复车体坐标。

  roll 旋转方向若写反,此测试失败(d=15 处 dRel 偏差约 0.53m >> 0.01m)。
  """
  pitch, roll = 0.012, 0.02
  for d, y in [(15.0, 1.0), (35.0, -1.5)]:
    u_full, v_full = _forward_project(d, y, C.CAMERA_HEIGHT, pitch, roll)
    pt = project_box_to_vehicle(u=u_full, v=v_full, fx=FX, fy=FY, cx=CX, cy=CY,
                                height=C.CAMERA_HEIGHT, pitch=pitch, yaw=0.0,
                                roll=roll, camera_to_front=0.0)
    assert pt is not None
    assert abs(pt["dRel"] - d) < 0.01 and abs(pt["yRel"] - y) < 0.01
