"""avoidanceDebug publishing tests (daemon level, stub camera + detector)."""

import pytest

from openpilot.cereal.services import SERVICE_LIST
from openpilot.selfdrive.avoidanced import constants as C
from openpilot.selfdrive.avoidanced.tests.test_daemon_fusion import ROI, _FakeCamera, _FakeDetector, _box_at, _daemon

MODEL_CURVATURE = 0.012


def _debug_msgs(pm):
  return [msg for service, msg in pm.sent if service == 'avoidanceDebug']


def test_services_entry_is_5hz_and_not_logged():
  svc = SERVICE_LIST["avoidanceDebug"]
  assert svc.frequency == 5.
  assert svc.should_log is False


def test_debug_message_sent_every_frame_even_invalid():
  daemon, pm = _daemon(radar_points=[])
  daemon.update(0.0)
  daemon.update(C.DT_5HZ)
  debug = [(s, m) for s, m in pm.sent if s == 'avoidanceDebug']
  plans = [(s, m) for s, m in pm.sent if s == 'lateralManeuverPlan']
  assert len(debug) == len(plans) == 2   # every-frame publish, both services
  assert all(m.valid is True for _, m in debug)   # observation is always valid
  assert debug[-1][1].avoidanceDebug.valid is False   # planner validity lives in the struct


def test_debug_targets_radar_and_vision_with_matching_pair_ids():
  # One radar point and one YOLO box at the same spot: both carry matched=True
  # and the same non-zero pairId; nothing else gets a pair.
  person = _box_at(20.0, -1.8, cls="person", conf=0.8)
  far_car = _box_at(60.0, 0.0, cls="car", conf=0.9)  # outside the planner gate
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[person, far_car]),
                       radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].avoidanceDebug

  radar_t = [t for t in dbg.targets if not t.vision]
  vision_t = [t for t in dbg.targets if t.vision]
  assert len(radar_t) == 1 and len(vision_t) == 2

  # radar side: vision-confirmed as person -> the vision class and the VRU
  # weight the planner actually used (not the old hardcoded vehicle default);
  # has vRel, matched to pair 1
  rt = [t for t in radar_t if t.dRel == pytest.approx(20.0)][0]
  assert rt.cls == "person" and rt.vision is False and rt.matched is True
  assert rt.pairId == 1 and rt.inGate is True
  assert rt.weight == pytest.approx(C.VRU_WEIGHT)

  # vision side: person matched (pair 1); far car unmatched (pairId 0, not in gate)
  vt = [t for t in vision_t if t.inGate][0]
  assert vt.cls == "person" and vt.vision is True and vt.matched is True
  assert vt.pairId == 1 and vt.conf == pytest.approx(0.8)
  assert vt.weight == pytest.approx(C.VRU_WEIGHT)
  out = [t for t in vision_t if not t.inGate][0]
  assert out.cls == "car" and out.matched is False and out.pairId == 0 and out.inGate is False

  assert dbg.nRadar == 1 and dbg.nVision == 2 and dbg.nAssociated == 1
  assert dbg.vEgo == pytest.approx(20.0)


def test_debug_radar_weight_matches_planner_weight_when_vision_confirmed():
  """A vision-confirmed VRU radar point is planned with the VRU weight
  (fuse_targets takes the vision class) — the debug row must report THAT
  weight, not the hardcoded vehicle default. Telemetry that disagrees with the
  action is how bugs stay invisible in shadow logs."""
  person = _box_at(20.0, -1.8, cls="person", conf=0.8)
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[person]),
                       radar_points=[(20.0, -1.8)])
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].avoidanceDebug
  rt = [t for t in dbg.targets if not t.vision][0]
  assert rt.matched is True
  assert rt.cls == "person"
  assert rt.weight == pytest.approx(C.VRU_WEIGHT)


def test_debug_counts_and_gate_flags_radar_only():
  # Radar-only degrade path still publishes debug with correct counts/gates.
  near, far = (8.0, -1.8), (80.0, 0.0)
  daemon, pm = _daemon(camera=_FakeCamera(), radar_points=[near, far])
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].avoidanceDebug
  assert dbg.nRadar == 2 and dbg.nVision == 0 and dbg.nAssociated == 0
  gates = {bool(t.inGate) for t in dbg.targets}
  assert gates == {True, False}
  assert all(not t.vision for t in dbg.targets)
  assert all(t.pairId == 0 and not t.matched for t in dbg.targets)
  assert dbg.valid is False  # planner not yet active (hysteresis)


def test_debug_carries_planner_state():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.8, cls="person")]))
  daemon.update(0.0)
  daemon.update(C.ENTER_HOLD_S + 0.01)
  dbg = _debug_msgs(pm)[-1].avoidanceDebug
  assert dbg.valid is True and dbg.active is True
  assert dbg.direction == 1  # target on the right -> avoid left
  assert dbg.yDes > 0.0
  assert dbg.maxOffset == pytest.approx(C.MAX_OFFSET_FREE)
  assert dbg.edgeClearance == 999.0  # no road edges -> inf sent as 999.0


def test_debug_bsm_flags_from_car_state():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 2),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       radar_points=[])
  # patch blindspot flags after construction
  daemon.sm._data["carState"].leftBlindspot = False
  daemon.sm._data["carState"].rightBlindspot = True
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].avoidanceDebug
  assert dbg.bsmLeft is False and dbg.bsmRight is True


def test_debug_carries_radar_error_flags():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI]),
                       detector=_FakeDetector(detections=[_box_at(20.0, -1.0, cls="person")]),
                       radar_points=[])
  daemon.sm._data["radarTracks"].errors.canError = True
  daemon.sm._data["radarTracks"].errors.radarUnavailableTemporary = True
  daemon.update(0.0)
  dbg = _debug_msgs(pm)[-1].avoidanceDebug
  assert dbg.canError is True and dbg.radarUnavailable is True
