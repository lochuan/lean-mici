"""DeviceHealth: adaptive vision cadence (StarPilot port).

Covers the four interval sources (base / processing-cost / cpu factor /
memory pressure) and the locationd inputsOK backoff, plus the daemon-level
skip that keeps the streams publishing while inference is deferred.
"""

import time

import pytest

from openpilot.selfdrive.eagled.device_health import (
  DeviceHealth,
  device_cpu_throttle_factor,
  memory_pressure_level,
)
from openpilot.selfdrive.eagled.eagled import EagleDaemon, VISION_BASE_INTERVAL
from openpilot.selfdrive.eagled.tests.test_daemon_fusion import (
  MODEL_CURVATURE,
  ROI,
  _FakeCamera,
  _FakeDetector,
  _FakePubMaster,
  _FakeSubMaster,
  _NS,
  _box_at,
)


class _MutableParams:
  def __init__(self):
    self.enabled = False

  def get_bool(self, key, block=False):
    return self.enabled

  def get(self, key, block=False, return_default=False):
    return None


def _healthy_sm():
  return _FakeSubMaster(
    _NS(action=_NS(desiredCurvature=0.012), roadEdges=[], meta=_NS(laneChangeState="off")),
    _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False),
    _NS(points=[], errors=_NS(canError=False, radarUnavailableTemporary=False)))


def _vision_daemon(detector, camera, params):
  model_v2 = _NS(action=_NS(desiredCurvature=MODEL_CURVATURE), roadEdges=[],
                 meta=_NS(laneChangeState="off"))
  car_state = _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False)
  radar = _NS(points=[], errors=_NS(canError=False, radarUnavailableTemporary=False))
  pm = _FakePubMaster()
  sm = _FakeSubMaster(model_v2, car_state, radar,
                      valid={"modelV2": True, "carState": True, "radarTracks": True,
                             "deviceState": True, "procLog": True, "deviceMotion": True})
  daemon = EagleDaemon(sm=sm, pm=pm, params=params, camera=camera, detector=detector)
  return daemon, pm, sm


# --- device_cpu_throttle_factor ----------------------------------------------------


def test_cpu_factor_one_when_idle():
  # fresh state per name -> no leftover filter state across tests
  assert device_cpu_throttle_factor([10.0] * 8, name="t1") == 1.0


def test_cpu_factor_rises_with_hot_cores():
  # 8 cores at 100%: hot-core factor 3.5. The low-pass swallows the first
  # reading (dt~0 -> alpha~0), so pump several calls: the factor must climb.
  factor = 1.0
  for i in range(6):
    factor = device_cpu_throttle_factor([100.0] * 8, name="t2")
    time.sleep(0.02)
  assert factor > 1.05


# --- memory_pressure_level ----------------------------------------------------------


def test_memory_pressure_levels():
  assert memory_pressure_level(3 * 1024 * 1024, 40.0) == "normal"
  assert memory_pressure_level(600 * 1024, 60.0) == "normal"
  assert memory_pressure_level(400 * 1024, 60.0) == "pressure"
  assert memory_pressure_level(300 * 1024, 95.0) == "critical"
  assert memory_pressure_level(None, 96.0) == "critical"
  assert memory_pressure_level(None, None) == "normal"


# --- DeviceHealth.inference_interval -------------------------------------------------


def test_interval_base_when_healthy():
  h = DeviceHealth()
  h.refresh(_healthy_sm())
  interval, reason = h.inference_interval(0.0, VISION_BASE_INTERVAL, 0.0)
  assert interval == pytest.approx(VISION_BASE_INTERVAL)
  assert reason == "steady"


def test_interval_stretches_with_inference_cost():
  h = DeviceHealth()
  h.refresh(_healthy_sm())
  # 85ms real-world pass * 2.5 -> ~213ms, above the 200ms base
  interval, reason = h.inference_interval(0.0, VISION_BASE_INTERVAL, 0.085)
  assert reason == "processing_cost"
  assert interval == pytest.approx(0.085 * 2.5)


def test_interval_memory_pressure_overrides():
  sm = _healthy_sm()
  sm._data["deviceState"].memoryUsagePercent = 95.0
  h = DeviceHealth()
  h.refresh(sm)
  interval, reason = h.inference_interval(0.0, VISION_BASE_INTERVAL, 0.0)
  assert reason == "memory_critical"
  assert interval >= 2.0


def test_interval_locationd_recovery_window():
  sm = _healthy_sm()
  sm._data["deviceMotion"].inputsOK = False
  h = DeviceHealth()
  h.refresh(sm)
  assert h.inputs_ok is False
  interval, reason = h.inference_interval(0.0, VISION_BASE_INTERVAL, 0.0)
  assert reason == "locationd_recovery"
  assert interval >= 1.0


def test_health_degrades_gracefully_on_missing_services():
  # minimal fake: only the four fusion services, no health services at all
  sm = _FakeSubMaster(
    _NS(action=_NS(desiredCurvature=0.012), roadEdges=[], meta=_NS(laneChangeState="off")),
    _NS(vEgo=20.0, leftBlindspot=False, rightBlindspot=False, steeringPressed=False),
    _NS(points=[], errors=_NS(canError=False, radarUnavailableTemporary=False)))
  sm.valid = dict.fromkeys(("modelV2", "carState", "radarTracks"), True)
  h = DeviceHealth()
  h.refresh(sm)  # must not raise
  interval, _ = h.inference_interval(0.0, VISION_BASE_INTERVAL, 0.0)
  assert interval == VISION_BASE_INTERVAL


# --- daemon-level: inputsOK false defers vision, streams keep flowing ----------------


def test_daemon_defers_vision_while_locationd_unhealthy():
  params = _MutableParams()
  params.enabled = True
  detector = _FakeDetector(detections=[_box_at(20.0, -1.0)])
  camera = _FakeCamera(frames=[ROI] * 3)
  daemon, pm, sm = _vision_daemon(detector, camera, params)
  sm._data["deviceMotion"].inputsOK = False

  daemon.update(0.0)                     # first tick: vision runs (interval starts at 0)
  assert detector.calls == 1
  daemon.update(0.2)                     # recovery window (real-time) -> deferred
  assert detector.calls == 1
  # streams still publish every tick — only inference is deferred
  states = [msg for service, msg in pm.sent if service == "eagleState"]
  assert len(states) == 2
  assert states[-1].eagleState.nVision == 0
  plans = [msg for service, msg in pm.sent if service == "lateralManeuverPlan"]
  assert len(plans) == 2
  # after the recovery interval elapses, inference resumes
  daemon.update(1.3)
  assert detector.calls == 2


def test_daemon_memory_pressure_stretches_interval():
  params = _MutableParams()
  params.enabled = True
  detector = _FakeDetector(detections=[])
  camera = _FakeCamera(frames=[ROI] * 3)
  daemon, pm, sm = _vision_daemon(detector, camera, params)
  sm._data["deviceState"].memoryUsagePercent = 95.0

  daemon.update(0.0)
  assert detector.calls == 1
  # 2s memory interval: 0.2s and 1.0s later ticks stay deferred
  daemon.update(0.2)
  daemon.update(1.0)
  assert detector.calls == 1
  # streams keep flowing while inference is deferred
  assert len([msg for service, msg in pm.sent if service == "lateralManeuverPlan"]) == 3

  # once memory recovers, cadence returns to base
  sm._data["deviceState"].memoryUsagePercent = 50.0
  daemon.update(2.1)
  assert detector.calls == 2
