#!/usr/bin/env python3
"""avoidanced: 5Hz camera+radar fused lateral avoidance bias on top of model curvature.

Per tick the full fusion chain runs: wide-road camera frame -> YOLO ROI
inference -> ground-plane projection into the car frame -> radar association ->
``fuse_targets`` -> planner. Publishes ``lateralManeuverPlan`` **every frame**
with the message envelope ``valid`` flag set from the planner. An invalid frame
(no target, gated, takeover, disabled) still carries the raw model curvature, so
controlsd falls back cleanly; nothing depends on the message going stale.

When the camera stream or the YOLO weights are unavailable the daemon degrades
to radar-only: it logs the reason once and keeps publishing radar-fused plans.
"""

import os
from pathlib import Path

from openpilot.common.hardware import COMMA_HARDWARE

# The YOLO tinygrad pkl is compiled for QCOM (Task 4); tinygrad selects its
# backend from DEV at import time. Export it here so any lazy TinygradRunner in
# this process lands on the QCOM backend instead of CPU.
if COMMA_HARDWARE:
  os.environ.setdefault("DEV", "QCOM")

import time

import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, Ratekeeper, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.association import associate
from openpilot.selfdrive.avoidanced.avoidance_planner import AvoidancePlanner, fuse_targets
from openpilot.selfdrive.avoidanced.camera_stream import CameraStream
from openpilot.selfdrive.avoidanced.projection import project_detections
from openpilot.selfdrive.avoidanced.yolo_detector import YoloDetector

PARAMS_REFRESH_PERIOD = 1.0  # s
YOLO_PKL_PATH = Path(__file__).parent / "models" / "yolo_tinygrad.pkl"


class AvoidanceDaemon:
  def __init__(self, sm=None, pm=None, params=None, planner=None, camera=None, detector=None,
               camera_factory=CameraStream):
    self.params = params if params is not None else Params()
    self.sm = sm if sm is not None else messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
    self.pm = pm if pm is not None else messaging.PubMaster(['lateralManeuverPlan'])
    self.planner = planner if planner is not None else AvoidancePlanner()
    self.camera = camera                  # lazy: created via camera_factory on first use
    self.camera_factory = camera_factory
    self.detector = detector              # lazy: YoloDetector on first use
    self.detector_dead = False            # YOLO failed hard -> stop retrying
    self.degraded: set[str] = set()       # radar-only fallback reasons, logged once each
    self.max_offset = C.MAX_OFFSET_FREE
    self.enabled = False
    self._last_params_t = -PARAMS_REFRESH_PERIOD

  def _refresh_params(self, now: float) -> None:
    if now - self._last_params_t < PARAMS_REFRESH_PERIOD:
      return
    self._last_params_t = now
    self.enabled = self.params.get_bool("AvoidanceEnabled")
    try:
      value = self.params.get("AvoidanceMaxLateralOffset")
    except Exception:
      value = None
    if value is not None:
      self.max_offset = float(np.clip(float(value), 0.0, C.MAX_OFFSET_FREE))

  def _degrade(self, reason: str) -> None:
    """Log a radar-only fallback reason once (never spam)."""
    if reason not in self.degraded:
      self.degraded.add(reason)
      cloudlog.warning(f"avoidanced: {reason} unavailable, radar-only fallback")

  def _detect(self, now: float) -> list[dict]:
    """Camera -> YOLO -> car-frame projections; ``[]`` keeps the frame radar-only."""
    if self.camera is None:
      self.camera = self.camera_factory()
    frame = self.camera.frame()
    if frame is None:
      # No camerad stream (PC) or no fresh frame this tick; connect keeps
      # retrying inside CameraStream, the reason is only logged once.
      self._degrade("camera")
      return []
    roi, roi_meta = frame
    if self.detector is None:
      self.detector = YoloDetector(YOLO_PKL_PATH)
    if self.detector_dead:
      return []
    try:
      detections = self.detector.infer(roi, now=now)
    except Exception:
      # Missing pkl (weights are not in the repo) or a hard inference failure:
      # radar-only from here on, logged once, never crash the daemon.
      self.detector_dead = True
      self._degrade("yolo")
      cloudlog.exception("avoidanced: YOLO inference failed")
      return []
    fx, fy, cx, cy = self.camera.intrinsics
    return project_detections(detections, fx=fx, fy=fy, cx=cx, cy=cy,
                              height=C.CAMERA_HEIGHT, pitch=C.CAMERA_PITCH, yaw=C.CAMERA_YAW,
                              camera_to_front=C.CAMERA_TO_FRONT, roi_meta=roi_meta)

  def update(self, now: float) -> None:
    self._refresh_params(now)
    self.sm.update(0)

    model_v2 = self.sm['modelV2']
    car_state = self.sm['carState']
    radar = self.sm['radarTracks']

    detections = self._detect(now)
    n_associated, fused = associate(radar.points, detections)
    targets = fuse_targets(radar.points, fused)
    curvature, valid = self.planner.update(
      model_curvature=model_v2.action.desiredCurvature,
      targets=targets,
      v_ego=car_state.vEgo,
      bsm_left=car_state.leftBlindspot,
      bsm_right=car_state.rightBlindspot,
      road_edges=model_v2.roadEdges,
      enabled=self.enabled,
      steering_pressed=car_state.steeringPressed,
      max_offset=self.max_offset,
      now=now,
    )

    # Publish every frame. ``valid`` is the message envelope flag controlsd reads
    # via ``sm.valid['lateralManeuverPlan']``; an invalid plan still carries the
    # model curvature so a fresh-but-invalid frame falls back cleanly.
    # Defensive gate: if this frame's modelV2 failed validation its curvature is
    # suspect, so the plan is never published as valid. We keep sending (instead
    # of skipping the frame) to preserve the every-frame freshness invariant in
    # controlsd; the invalid envelope makes controlsd ignore the curvature.
    msg = messaging.new_message('lateralManeuverPlan')
    msg.lateralManeuverPlan.desiredCurvature = float(curvature)
    msg.valid = bool(valid) and bool(self.sm.valid['modelV2'])
    self.pm.send('lateralManeuverPlan', msg)


def main() -> None:
  config_realtime_process([0, 1, 2, 3], Priority.CTRL_LOW)
  cloudlog.info("avoidanced starting")
  daemon = AvoidanceDaemon()
  rk = Ratekeeper(5.0)
  while True:
    daemon.update(time.monotonic())
    rk.keep_time()


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    cloudlog.warning("got SIGINT")
