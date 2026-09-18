#!/usr/bin/env python3
"""avoidanced: 5Hz radar-fused lateral avoidance bias on top of model curvature.

Publishes ``lateralManeuverPlan`` only while the plan is valid. When the plan is
invalid (no target, gated, takeover) or the feature is disabled the message is
not sent, goes stale, and controlsd falls back to the model curvature.
"""

import os

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
from openpilot.selfdrive.avoidanced.avoidance_planner import AvoidancePlanner, edge_clearance, fuse_targets

PARAMS_REFRESH_PERIOD = 1.0  # s


class AvoidanceDaemon:
  def __init__(self, sm=None, pm=None, params=None, planner=None):
    self.params = params if params is not None else Params()
    self.sm = sm if sm is not None else messaging.SubMaster(['modelV2', 'carState', 'radarTracks'])
    self.pm = pm if pm is not None else messaging.PubMaster(['lateralManeuverPlan'])
    self.planner = planner if planner is not None else AvoidancePlanner()
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

  def update(self, now: float) -> None:
    self._refresh_params(now)
    self.sm.update(0)

    model_v2 = self.sm['modelV2']
    car_state = self.sm['carState']
    radar = self.sm['radarTracks']

    targets = fuse_targets(radar.points)
    curvature, valid = self.planner.update(
      model_curvature=model_v2.action.desiredCurvature,
      targets=targets,
      v_ego=car_state.vEgo,
      bsm_left=car_state.leftBlindspot,
      bsm_right=car_state.rightBlindspot,
      clearance=edge_clearance(model_v2.roadEdges),
      enabled=self.enabled,
      steering_pressed=car_state.steeringPressed,
      max_offset=self.max_offset,
      now=now,
    )

    # Publish every frame. ``valid`` is the message envelope flag controlsd reads
    # via ``sm.valid['lateralManeuverPlan']``; an invalid plan still carries the
    # model curvature so a fresh-but-invalid frame falls back cleanly.
    msg = messaging.new_message('lateralManeuverPlan')
    msg.lateralManeuverPlan.desiredCurvature = float(curvature)
    msg.valid = bool(valid)
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
