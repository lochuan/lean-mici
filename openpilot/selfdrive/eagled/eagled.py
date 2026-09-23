#!/usr/bin/env python3
"""eagled: 5Hz lateral-situation perception layer + the avoidance consumer.

The perception core (``perception.PerceptionCore``) fuses radar tracks with
wide-camera YOLO detections into the per-frame target picture, published as
``eagleState`` (the formal stream for consumers — desire_helper in modeld is
the planned second one) alongside ``eagleDebug`` (raw detection/association
telemetry for the lanlink UI and calibration — never a control input).

The avoidance planner (``avoidance_planner.AvoidancePlanner``) is the first
in-process consumer: it gates the fused targets (BSM / road-edge / speed /
lane-change) and produces a small curvature bias, published as
``lateralManeuverPlan`` **every frame** with the envelope ``valid`` flag set
from the planner. An invalid frame (no target, gated, takeover, disabled)
still carries the raw model curvature, so controlsd falls back cleanly;
nothing depends on the message going stale.

Perception runs whenever the device is onroad in a car; avoidance actuation
is gated separately by the ``AvoidanceEnabled`` param — turning avoidance
off never turns the eagle's eyes off. When the camera stream, calibration
or the YOLO weights are unavailable the core degrades to radar-only: the
reason is logged once and radar-fused frames keep publishing.
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
from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.avoidance_planner import AvoidancePlanner
from openpilot.selfdrive.eagled.camera_stream import CameraStream
from openpilot.selfdrive.eagled.perception import PerceptionCore, PerceptionFrame, gate_target, radar_point_key

PARAMS_REFRESH_PERIOD = 1.0  # s


class EagleDaemon:
  def __init__(self, sm=None, pm=None, params=None, planner=None, perception=None,
               camera=None, detector=None, camera_factory=CameraStream):
    self.params = params if params is not None else Params()
    self.sm = sm if sm is not None else messaging.SubMaster(
      ['modelV2', 'carState', 'radarTracks', 'extrinsicsCalibration'])
    self.pm = pm if pm is not None else messaging.PubMaster(['eagleDebug', 'eagleState', 'lateralManeuverPlan'])
    self.planner = planner if planner is not None else AvoidancePlanner()
    self.perception = perception if perception is not None else PerceptionCore(
      camera=camera, detector=detector, camera_factory=camera_factory)
    self.max_offset = C.MAX_OFFSET_FREE
    self.enabled = False
    self._last_params_t = -PARAMS_REFRESH_PERIOD

  # The camera/YOLO lifecycle lives on the perception core; these read-only
  # views keep the historical daemon surface (tests, shadow tooling) working.
  @property
  def camera(self):
    return self.perception.camera

  @property
  def detector(self):
    return self.perception.detector

  @property
  def detector_dead(self) -> bool:
    return self.perception.detector_dead

  @property
  def degraded(self) -> set[str]:
    return self.perception.degraded

  def _refresh_params(self, now: float) -> None:
    if now - self._last_params_t < PARAMS_REFRESH_PERIOD:
      return
    self._last_params_t = now
    # AvoidanceEnabled gates only the lateralManeuverPlan actuation, never the
    # eagleState/eagleDebug perception streams.
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

    frame = self.perception.process(self.sm, now, car_state.vEgo)
    # Suppress the bias during lane changes: the model curvature is already
    # executing a large lateral manoeuvre and the target's relative bearing is
    # changing fast, so a bias derived from "target is on the left/right" on
    # top of it is unpredictable. laneChangeState lives on modelV2.meta
    # (log.capnp MetaData) — no new subscription needed.
    lane_change_active = str(model_v2.meta.laneChangeState) != "off"
    curvature, valid = self.planner.update(
      model_curvature=model_v2.action.desiredCurvature,
      targets=frame.targets,
      v_ego=car_state.vEgo,
      budget_left=frame.left.budget,
      budget_right=frame.right.budget,
      road_edges=model_v2.roadEdges,
      enabled=self.enabled,
      steering_pressed=car_state.steeringPressed,
      lane_change_active=lane_change_active,
      max_offset=self.max_offset,
      now=now,
      road_edge_stds=getattr(model_v2, "roadEdgeStds", None),
    )

    # Publish order per frame: eagleDebug (raw telemetry) -> eagleState (the
    # picture) -> lateralManeuverPlan (the plan) LAST so the every-frame
    # freshness invariant tests reading pm.sent[-1] keep holding. ``valid`` on
    # the plan is the envelope flag controlsd reads via
    # ``sm.valid['lateralManeuverPlan']``; an invalid plan still carries the
    # model curvature so a fresh-but-invalid frame falls back cleanly.
    # Defensive gate: if this frame's modelV2 failed validation its curvature
    # is suspect, so the plan is never published as valid. We keep sending
    # (instead of skipping the frame) to preserve the every-frame freshness
    # invariant in controlsd; the invalid envelope makes controlsd ignore the
    # curvature.
    self._publish_debug(frame, car_state, valid=bool(valid), radar_errors=radar.errors)
    self._publish_state(frame, car_state, radar_errors=radar.errors)

    msg = messaging.new_message('lateralManeuverPlan')
    msg.lateralManeuverPlan.desiredCurvature = float(curvature)
    msg.valid = bool(valid) and bool(self.sm.valid['modelV2'])
    self.pm.send('lateralManeuverPlan', msg)

  def _target_rows(self, frame: PerceptionFrame) -> list[tuple[bool, dict]]:
    """One row per radar point and per detection: (in_gate, serialized fields).

    Shared by both publishers so eagleState and eagleDebug can never disagree
    about what was seen. The in/out verdict goes through the SAME
    ``gate_target`` that fed fuse_targets this frame — telemetry that
    re-derives its own gate would silently disagree with lane-relative mode.
    Association pairs reference this frame's radar/detection objects, so match
    them back by identity to stamp the shared pairId on both sides. Radar
    identity must go through radar_point_key: this list is a fresh capnp
    re-iteration, so id() would never match the pairs' objects (vision dicts
    are plain Python objects, id() is stable for them).
    """
    radar_pair_ids = {radar_point_key(p[0]): p[2] for p in frame.pairs}
    vision_pair_ids = {id(p[1]): p[2] for p in frame.pairs}
    rows: list[tuple[bool, dict]] = []
    for point in frame.radar_points:
      key = radar_point_key(point)
      # Same weight the planner used: a vision-confirmed radar point takes the
      # vision class weight (fuse_targets), not the vehicle default.
      cls = frame.vision_cls_by_key.get(key)
      in_gate, lane = gate_target(float(point.dRel), float(point.yRel), cls, frame.lane_geo)
      rows.append((in_gate, {
        "dRel": float(point.dRel), "yRel": float(point.yRel), "vRel": float(point.vRel),
        "cls": cls or "", "conf": 0.0,
        "weight": C.class_weight(cls),
        "matched": key in radar_pair_ids, "inGate": in_gate, "vision": False,
        "pairId": radar_pair_ids.get(key, 0),
        "lane": lane,
      }))
    for det in frame.detections:
      cls = det.get("cls")
      in_gate, lane = gate_target(float(det["dRel"]), float(det["yRel"]), cls, frame.lane_geo)
      weight = C.class_weight(cls)
      rows.append((in_gate, {
        "dRel": float(det["dRel"]), "yRel": float(det["yRel"]), "vRel": 0.0,
        "cls": cls or "", "conf": float(det.get("conf", 1.0)),
        "weight": weight,
        "matched": id(det) in vision_pair_ids, "inGate": in_gate, "vision": True,
        "pairId": vision_pair_ids.get(id(det), 0),
        "lane": lane,
      }))
    return rows

  def _publish_debug(self, frame: PerceptionFrame, car_state, valid: bool, radar_errors=None) -> None:
    """Build and publish the fused eagleDebug snapshot for this frame.

    Sent every frame regardless of planner validity: the message envelope
    ``valid`` flag is always true (this is a live observation, not a plan), and
    the planner's own validity lives in the struct's ``valid`` field. Consumers
    are lanlink's bird's-eye view and the calibration tool — never controlsd.
    """
    msg = messaging.new_message('eagleDebug')
    dbg = msg.eagleDebug
    last = self.planner.last_state
    dbg.valid = bool(valid)
    dbg.active = bool(last.get("active", False))
    dbg.direction = int(last.get("direction", 0))
    dbg.yDes = float(last.get("yDes", 0.0))
    dbg.bias = float(last.get("bias", 0.0))
    dbg.maxOffset = float(last.get("maxOffset", self.max_offset))
    dbg.bsmLeft = bool(car_state.leftBlindspot)
    dbg.bsmRight = bool(car_state.rightBlindspot)
    dbg.vEgo = float(car_state.vEgo)
    dbg.nRadar = len(frame.radar_points)
    dbg.nVision = len(frame.detections)
    dbg.nAssociated = int(frame.n_associated)
    dbg.edgeClearance = min(float(last.get("edgeClearance", float("inf"))), 999.0)
    dbg.canError = bool(radar_errors.canError) if radar_errors is not None else False
    dbg.radarUnavailable = bool(radar_errors.radarUnavailableTemporary) if radar_errors is not None else False
    geo = frame.lane_geo
    dbg.laneLeftValid = bool(geo.left_valid) if geo is not None else False
    dbg.laneRightValid = bool(geo.right_valid) if geo is not None else False
    dbg.budgetLeft = float(last.get("budgetLeft", C.BUDGET_UNCONSTRAINED))
    dbg.budgetRight = float(last.get("budgetRight", C.BUDGET_UNCONSTRAINED))
    dbg.changeClearLeft = bool(frame.left.change_clear)
    dbg.changeClearRight = bool(frame.right.change_clear)
    rows = self._target_rows(frame)
    tgts = dbg.init('targets', len(rows))
    for i, (in_gate, t) in enumerate(rows):
      tgts[i].dRel = t["dRel"]
      tgts[i].yRel = t["yRel"]
      tgts[i].vRel = t["vRel"]
      tgts[i].cls = t["cls"]
      tgts[i].conf = t["conf"]
      tgts[i].weight = t["weight"]
      tgts[i].matched = t["matched"]
      tgts[i].inGate = in_gate
      tgts[i].vision = t["vision"]
      tgts[i].pairId = t["pairId"]
      tgts[i].lane = t["lane"]
    msg.valid = True
    self.pm.send('eagleDebug', msg)

  def _publish_state(self, frame: PerceptionFrame, car_state, radar_errors=None) -> None:
    """Publish eagleState: the formal per-frame perception picture.

    Same data as eagleDebug minus the debug-only noise (decision snapshot,
    pair ids, out-of-gate rows): in-gate fused targets, side inputs (BSM),
    geometry and sensor health. This is the stream future consumers
    (desire_helper in modeld) subscribe to; the envelope ``valid`` is always
    true — the picture is an observation, planner validity lives in the plan.
    """
    msg = messaging.new_message('eagleState')
    st = msg.eagleState
    last = self.planner.last_state
    st.bsmLeft = bool(car_state.leftBlindspot)
    st.bsmRight = bool(car_state.rightBlindspot)
    st.vEgo = float(car_state.vEgo)
    st.edgeClearance = min(float(last.get("edgeClearance", float("inf"))), 999.0)
    st.nRadar = len(frame.radar_points)
    st.nVision = len(frame.detections)
    st.nAssociated = int(frame.n_associated)
    st.canError = bool(radar_errors.canError) if radar_errors is not None else False
    st.radarUnavailable = bool(radar_errors.radarUnavailableTemporary) if radar_errors is not None else False
    geo = frame.lane_geo
    st.laneLeftValid = bool(geo.left_valid) if geo is not None else False
    st.laneRightValid = bool(geo.right_valid) if geo is not None else False
    st.budgetLeft = frame.left.budget
    st.budgetRight = frame.right.budget
    st.changeClearLeft = bool(frame.left.change_clear)
    st.changeClearRight = bool(frame.right.change_clear)
    for field, picture in ((st.sideLeadLeft, frame.left), (st.sideLeadRight, frame.right)):
      lead = picture.lead
      field.valid = lead is not None
      if lead is not None:
        field.dRel = lead.dRel
        field.yRel = lead.yRel
        field.vRel = lead.vRel if lead.vRel is not None else 0.0
        field.edgeDist = abs(lead.yRel) - C.class_half_width(lead.cls)
        field.cls = lead.cls or ""
    rows = [t for in_gate, t in self._target_rows(frame) if in_gate]
    tgts = st.init('targets', len(rows))
    for i, t in enumerate(rows):
      tgts[i].dRel = t["dRel"]
      tgts[i].yRel = t["yRel"]
      tgts[i].vRel = t["vRel"]
      tgts[i].cls = t["cls"]
      tgts[i].conf = t["conf"]
      tgts[i].weight = t["weight"]
      tgts[i].matched = t["matched"]
      tgts[i].inGate = True
      tgts[i].vision = t["vision"]
      tgts[i].pairId = t["pairId"]
      tgts[i].lane = t["lane"]
    msg.valid = True
    self.pm.send('eagleState', msg)


def main() -> None:
  config_realtime_process([0, 1, 2, 3], Priority.CTRL_LOW)
  cloudlog.info("eagled starting")
  daemon = EagleDaemon()
  rk = Ratekeeper(5.0)
  while True:
    daemon.update(time.monotonic())
    rk.keep_time()


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    cloudlog.warning("got SIGINT")
