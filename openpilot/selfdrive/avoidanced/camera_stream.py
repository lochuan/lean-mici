"""Wide-road camera feed for avoidanced.

Connects to camerad's ``VISION_STREAM_WIDE_ROAD`` over visionipc, converts the
NV12 buffer to RGB (same path as ``system/camerad/snapshot.py``), crops the
bottom ROI and resizes it to the YOLO input (640x384). On a machine without
camerad (e.g. a PC) ``frame()`` simply returns ``None`` — connect retries are
throttled so the daemon falls back to radar-only without log spam.
"""

from __future__ import annotations

import time

import cv2
import numpy as np
from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.selfdrive.avoidanced.constants import ROI_MODE, ROI_MODE_NATIVE
from openpilot.selfdrive.avoidanced.projection import RoiMeta, roi_meta_for
from openpilot.selfdrive.avoidanced.yolo_detector import INPUT_H, INPUT_W

CAMERAD_NAME = "camerad"
CONNECT_RETRY_S = 1.0

# comma 4 (mici, os04c10) wide road camera intrinsics; scaled to the actual
# buffer resolution so a resolution change keeps the projection consistent.
_WIDE_CAM = DEVICE_CAMERAS[("mici", "os04c10")].wide_road


def scaled_intrinsics(width: float, height: float) -> tuple[float, float, float, float]:
  """Wide-camera ``(fx, fy, cx, cy)`` scaled from the native to the buffer resolution."""
  fx = _WIDE_CAM.focal_length * width / _WIDE_CAM.width
  fy = _WIDE_CAM.focal_length * height / _WIDE_CAM.height
  return float(fx), float(fy), float(width) / 2.0, float(height) / 2.0


def resize_bilinear(img: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
  """Bilinear resize ``(H, W, C) -> (out_h, out_w, C)``, pixel-centre aligned."""
  h, w = img.shape[:2]
  if (h, w) == (out_h, out_w):
    return img
  u = np.clip((np.arange(out_w, dtype=np.float32) + 0.5) * (w / out_w) - 0.5, 0, w - 1)
  v = np.clip((np.arange(out_h, dtype=np.float32) + 0.5) * (h / out_h) - 0.5, 0, h - 1)
  u0, v0 = u.astype(np.int32), v.astype(np.int32)
  u1, v1 = np.minimum(u0 + 1, w - 1), np.minimum(v0 + 1, h - 1)
  wu = (u - u0).astype(np.float32)[None, :, None]
  wv = (v - v0).astype(np.float32)[:, None, None]
  src = img.astype(np.float32)
  top = src[v0][:, u0, :] * (1.0 - wu) + src[v0][:, u1, :] * wu
  bot = src[v1][:, u0, :] * (1.0 - wu) + src[v1][:, u1, :] * wu
  out = top * (1.0 - wv) + bot * wv
  return np.clip(out, 0, 255).astype(np.uint8)


def roi_from_rgb(rgb: np.ndarray, mode: str = ROI_MODE,
                 horizon_row: float | None = None) -> tuple[np.ndarray, RoiMeta]:
  """Full RGB frame -> YOLO ROI (384, 640, 3) plus the RoiMeta to invert it.

  SQUASH resizes; NATIVE is a pure slice, so it must not go through
  resize_bilinear at all -- resampling a 1:1 window would throw away the
  sharpness the mode exists to keep.
  """
  h, w = rgb.shape[:2]
  meta = roi_meta_for(w, h, mode=mode, horizon_row=horizon_row)
  if mode == ROI_MODE_NATIVE:
    top, left = int(meta.offset_v), int(meta.offset_u)
    roi = rgb[top:top + INPUT_H, left:left + INPUT_W]
  elif (h, w) != (INPUT_H, INPUT_W):
    crop_h = min(h, round(w * INPUT_H / INPUT_W))
    roi = cv2.resize(rgb[h - crop_h:], (INPUT_W, INPUT_H), interpolation=cv2.INTER_LINEAR)
  else:
    roi = rgb
  return np.ascontiguousarray(roi), meta


# Same BT.601 full-range matrix as system/camerad/snapshot.py -- the yolo
# pipeline must stay pixel-faithful to the conversion the model was validated
# against (cv2's YUV2RGB is limited-range and shifts the distribution).
_YUV_M = np.array([
  [1.00000,  1.00000, 1.00000],
  [0.00000, -0.39465, 2.03211],
  [1.13983, -0.58060, 0.00000],
])


def _nv12_to_roi_fast(buf, crop_h: int) -> np.ndarray:
  """NV12 buffer -> 640x384 RGB ROI in ~25 ms (legacy full-frame path: ~326 ms).

  Geometry via OpenCV (3 plane resizes in C), color via the exact float matrix
  on the small frame. Resize and YUV->RGB are both linear, so resizing the
  planes first and converting after is the same mapping as the legacy
  convert-then-resize, up to float rounding (validated <=2 LSB on device).
  """
  h, w, stride = buf.height, buf.width, buf.stride
  data = np.asarray(buf.data, dtype=np.uint8)
  y = data[:stride * h].reshape(h, stride)[:, :w][h - crop_h:]
  uv = data[buf.uv_offset:buf.uv_offset + stride * (h // 2)].reshape(h // 2, stride)[:, :w][(h - crop_h) // 2:]
  y_s = cv2.resize(y, (INPUT_W, INPUT_H), interpolation=cv2.INTER_LINEAR)
  u_s = cv2.resize(uv[::2], (INPUT_W, INPUT_H), interpolation=cv2.INTER_LINEAR)
  v_s = cv2.resize(uv[1::2], (INPUT_W, INPUT_H), interpolation=cv2.INTER_LINEAR)
  yuv = np.dstack((y_s, u_s.astype(np.int16) - 128, v_s.astype(np.int16) - 128))
  return np.clip(yuv @ _YUV_M, 0, 255).astype(np.uint8)


def _nv12_to_rgb_cv(buf) -> np.ndarray:
  """NV12 buffer -> full-frame RGB via OpenCV. The visionipc buffer carries
  plane padding (Y rows aligned up to 16, uv_offset lands past them, extra
  tail), so rebuild the packed NV12 layout cv2 expects: h Y rows then h//2
  interleaved UV rows.
  """
  h, w, stride = buf.height, buf.width, buf.stride
  data = np.asarray(buf.data, dtype=np.uint8)
  y = data[:stride * h].reshape(h, stride)[:, :w]
  uv = data[buf.uv_offset:buf.uv_offset + stride * (h // 2)].reshape(h // 2, stride)[:, :w]
  yuv = np.empty((h + h // 2, w), dtype=np.uint8)
  yuv[:h] = y
  yuv[h:] = uv
  return cv2.cvtColor(yuv, cv2.COLOR_YUV2RGB_NV12)


class CameraStream:
  """Throttled-retry visionipc client producing YOLO ROI frames."""

  def __init__(self, stream: VisionStreamType = VisionStreamType.VISION_STREAM_WIDE_ROAD,
               conflate: bool = True, clock=time.monotonic):
    self._stream = stream
    self._conflate = conflate
    self._clock = clock
    self._client: VisionIpcClient | None = None
    self.last_connect_t: float | None = None
    self.intrinsics: tuple[float, float, float, float] | None = None
    self.frame_size: tuple[int, int] | None = None

  def connect(self) -> bool:
    """Connect to camerad, retrying at most every ``CONNECT_RETRY_S``."""
    if self._client is not None and self._client.is_connected():
      return True
    now = self._clock()
    if self.last_connect_t is not None and (now - self.last_connect_t) < CONNECT_RETRY_S:
      return False
    self.last_connect_t = now
    if self._stream not in VisionIpcClient.available_streams(CAMERAD_NAME, block=False):
      return False
    client = VisionIpcClient(CAMERAD_NAME, self._stream, self._conflate)
    if not client.connect(False):
      return False
    self._client = client
    self.intrinsics = scaled_intrinsics(client.width, client.height)
    self.frame_size = (client.width, client.height)
    return True

  def frame(self, horizon_row: float | None = None) -> tuple[np.ndarray, RoiMeta] | None:
    """Latest ROI frame ``(roi uint8 (384, 640, 3), RoiMeta)``, or ``None``.

    ``horizon_row`` positions the NATIVE ROI window on the calibrated horizon;
    ``None`` keeps the historical frame-centre fallback.
    """
    if not self.connect() or self._client is None:
      return None
    buf = self._client.recv(0)
    if buf is None:
      return None
    if ROI_MODE == ROI_MODE_NATIVE:
      # Non-default mode keeps the legacy full-frame path (correctness first).
      return roi_from_rgb(_nv12_to_rgb_cv(buf), horizon_row=horizon_row)
    crop_h = min(buf.height, round(buf.width * INPUT_H / INPUT_W))
    meta = roi_meta_for(buf.width, buf.height, mode=ROI_MODE, horizon_row=horizon_row)
    return np.ascontiguousarray(_nv12_to_roi_fast(buf, crop_h)), meta
