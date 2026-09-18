"""Wide-road camera feed for avoidanced.

Connects to camerad's ``VISION_STREAM_WIDE_ROAD`` over visionipc, converts the
NV12 buffer to RGB (same path as ``system/camerad/snapshot.py``), crops the
bottom ROI and resizes it to the YOLO input (640x384). On a machine without
camerad (e.g. a PC) ``frame()`` simply returns ``None`` — connect retries are
throttled so the daemon falls back to radar-only without log spam.
"""

from __future__ import annotations

import time

import numpy as np
from msgq.visionipc import VisionIpcClient
from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.selfdrive.avoidanced.projection import RoiMeta, roi_meta_for
from openpilot.selfdrive.avoidanced.yolo_detector import INPUT_H, INPUT_W
from openpilot.system.camerad.snapshot import extract_image

CAMERAD_NAME = "camerad"
CONNECT_RETRY_S = 1.0

# comma 3X (mici, os04c10) wide road camera intrinsics; scaled to the actual
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


def roi_from_rgb(rgb: np.ndarray) -> tuple[np.ndarray, RoiMeta]:
  """Bottom-crop + resize a full RGB frame to the YOLO ROI; returns ``(roi, RoiMeta)``."""
  h, w = rgb.shape[:2]
  crop_h = min(h, round(w * INPUT_H / INPUT_W))
  roi = resize_bilinear(rgb[h - crop_h:], INPUT_W, INPUT_H)
  return np.ascontiguousarray(roi), roi_meta_for(w, h)


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
    return True

  def frame(self) -> tuple[np.ndarray, RoiMeta] | None:
    """Latest ROI frame ``(roi uint8 (384, 640, 3), RoiMeta)``, or ``None``."""
    if not self.connect() or self._client is None:
      return None
    buf = self._client.recv(0)
    if buf is None:
      return None
    return roi_from_rgb(extract_image(buf))
