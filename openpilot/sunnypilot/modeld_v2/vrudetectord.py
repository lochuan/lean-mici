#!/usr/bin/env python3
"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pickle
import time

import numpy as np

from openpilot.cereal import messaging
from openpilot.cereal.messaging import PubMaster, SubMaster
from openpilot.cereal.visionipc import VisionStreamType
from msgq.visionipc import VisionIpcClient
from openpilot.common.swaglog import cloudlog
from openpilot.common.realtime import config_realtime_process
from openpilot.common.transformations.camera import DEVICE_CAMERAS
from openpilot.common.file_chunker import open_file_chunked
from openpilot.selfdrive.modeld.helpers import MODELS_DIR, get_tg_input_devices
from tinygrad.tensor import Tensor

from openpilot.sunnypilot.modeld_v2.vru_preprocess import nv12_to_chw_rgb, MODEL_W, MODEL_H
from openpilot.sunnypilot.modeld_v2.vru_decode import decode_detections, ALLOWED_CLASSES
from openpilot.sunnypilot.modeld_v2.vru_projection import GroundProjector, DEFAULT_HEIGHT
from openpilot.sunnypilot.modeld_v2.vru_tracker import VRUTracker

PROCESS_NAME = "openpilot.sunnypilot.modeld_v2.vrudetectord"
MODEL_PKL_PATH = MODELS_DIR / 'vru_detect_tinygrad.pkl'
FRAME_SKIP = 4          # 20 Hz camera -> 5 Hz inference
MIN_RANGE = 0.5         # m, ignore detections closer than this (own hood / clipping)
MAX_RANGE = 100.0       # m


class VRUDetector:
  def __init__(self, cam_w: int, cam_h: int):
    self.DEV = get_tg_input_devices(PROCESS_NAME, usbgpu=False)['DEV']
    self.model_run = pickle.load(open_file_chunked(str(MODEL_PKL_PATH)))
    self.input_np = np.zeros((1, 3, MODEL_H, MODEL_W), dtype=np.float32)
    self.tensor_input = Tensor(self.input_np, device='NPY').realize()
    self.tracker = VRUTracker()
    self.projector: GroundProjector | None = None
    self.cam_w = cam_w
    self.cam_h = cam_h
    self._warned_unknown_cam = False

  def update_projector(self, sm: SubMaster) -> bool:
    if not (sm.seen['deviceState'] and sm.seen['wideRoadCameraState']):
      return False
    key = (str(sm['deviceState'].deviceType), str(sm['wideRoadCameraState'].sensor))
    cam_cfg = DEVICE_CAMERAS.get(key)
    if cam_cfg is None or cam_cfg.wide_road.focal_length == 0.0:
      if not self._warned_unknown_cam:
        cloudlog.warning("vrudetectord: unknown/invalid wide camera config, projector unavailable")
        self._warned_unknown_cam = True
      return False
    calib = sm['extrinsicsCalibration']
    if sm.seen['extrinsicsCalibration']:
      rpy = np.array(calib.rpyCalib, dtype=np.float64)
      wfe = np.array(calib.wideFromDeviceEuler, dtype=np.float64) if len(calib.wideFromDeviceEuler) == 3 else np.zeros(3)
      height = float(calib.height[0]) if len(calib.height) == 1 else DEFAULT_HEIGHT
    else:
      rpy, wfe = np.zeros(3), np.zeros(3)
      height = DEFAULT_HEIGHT
    self.projector = GroundProjector(cam_cfg.wide_road.intrinsics, rpy, wfe, height)
    return True

  def process_frame(self, buf) -> list[dict]:
    frame = nv12_to_chw_rgb(buf.data, buf.width, buf.height, buf.stride, buf.uv_offset)
    self.input_np[0] = frame
    output = self.model_run(images=self.tensor_input).numpy()
    dets = decode_detections(output, MODEL_H, MODEL_W, ALLOWED_CLASSES)
    ground = []
    for d in dets:
      u = (d['x1'] + d['x2']) / 2.0 * (self.cam_w / MODEL_W)
      v = d['y2'] * (self.cam_h / MODEL_H)
      pt = self.projector.pixel_to_ground(u, v)
      if pt is not None and MIN_RANGE < pt[0] < MAX_RANGE:
        ground.append({'classId': d['classId'], 'score': d['score'], 'x': pt[0], 'y': pt[1]})
    return self.tracker.update(ground)


def main():
  config_realtime_process(7, 5)

  vipc_client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_WIDE_ROAD, True)
  while not vipc_client.connect(False):
    time.sleep(0.1)
  assert vipc_client.is_connected()
  cloudlog.warning(f"vrudetectord: connected to wide road stream ({vipc_client.width}x{vipc_client.height})")

  detector = VRUDetector(vipc_client.width, vipc_client.height)
  cloudlog.warning("vrudetectord: models loaded, starting")

  sm = SubMaster(["deviceState", "wideRoadCameraState", "extrinsicsCalibration"])
  pm = PubMaster(["vruDetectionsSP"])

  while True:
    buf = vipc_client.recv()
    if buf is None:
      continue
    if vipc_client.frame_id % FRAME_SKIP != 0:
      continue

    sm.update(0)
    if not detector.update_projector(sm):
      continue

    t1 = time.perf_counter()
    tracks = detector.process_frame(buf)
    exec_time = time.perf_counter() - t1

    msg = messaging.new_message('vruDetectionsSP')
    m = msg.vruDetectionsSP
    m.frameId = vipc_client.frame_id
    m.timestampSof = vipc_client.timestamp_sof
    m.modelExecutionTime = exec_time
    dets = m.init('detections', len(tracks))
    for i, tr in enumerate(tracks):
      dets[i].classId = tr['classId']
      dets[i].score = tr['score']
      dets[i].x = tr['x']
      dets[i].y = tr['y']
      dets[i].trackId = tr['trackId']
      dets[i].vx = tr['vx']
    pm.send('vruDetectionsSP', msg)


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    cloudlog.warning("vrudetectord: got SIGINT")
