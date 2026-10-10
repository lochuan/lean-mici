import math
from types import SimpleNamespace as NS

import pytest

from opendbc.car.structs import car
from openpilot.cereal import messaging
from openpilot.common.realtime import DT_MDL
from openpilot.system.lanlinkd.diagnostics import Diagnostics


class LateralControlState:
  """lateralControlState 联合体的 duck-type：which() + 对应分支。"""

  def __init__(self, kind="torqueState", saturated=False, output=0.0):
    self._kind = kind
    setattr(self, kind, NS(saturated=saturated, output=output))

  def which(self):
    return self._kind


class FakeSM:
  def __init__(self):
    self.msgs = {
      "deviceState": NS(started=True),
      "carState": NS(vEgo=25.0, leftBlinker=False, rightBlinker=False, steeringPressed=False,
                     steerFaultTemporary=False, steerFaultPermanent=False, steeringAngleOffsetDeg=0.0),
      "carControl": NS(latActive=True, enabled=True),
      "controlsState": NS(desiredCurvature=0.0, curvature=0.0, lateralControlState=LateralControlState()),
      "modelV2": model(),
      "extrinsicsCalibration": None,
      "vehicleParameters": None,
      "lateralDelay": None,
    }
    self.updated = dict.fromkeys(self.msgs, False)

  def __getitem__(self, name):
    return self.msgs[name]


def line(y, xs=(0.0, 10.0, 20.0, 40.0, 80.0)):
  return NS(x=list(xs), y=[y] * len(xs))


def model(left=-1.8, right=1.8, plan_y=0.0, probs=0.9):
  ts = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
  return NS(laneLines=[line(-5.4), line(left), line(right), line(5.4)],
            laneLineProbs=[0.1, probs, probs, 0.1],
            position=NS(t=ts, x=[25.0 * t for t in ts], y=[plan_y] * len(ts)))


class Drive:
  """以 20 Hz 推进时间、喂消息的小驱动器。"""

  def __init__(self, diag=None, t0=100.0):
    self.diag = diag or Diagnostics()
    self.sm = FakeSM()
    self.now = t0
    self.sm.updated["deviceState"] = True
    self.diag.update(self.sm, self.now)
    self._clear()

  def _clear(self):
    for k in self.sm.updated:
      self.sm.updated[k] = False

  def frame(self, **model_kw):
    self.now += DT_MDL
    self.sm.msgs["modelV2"] = model(**model_kw)
    for k in ("modelV2", "carState", "carControl", "controlsState"):
      self.sm.updated[k] = True
    self.diag.update(self.sm, self.now)
    self._clear()

  def frames(self, n, **model_kw):
    for _ in range(n):
      self.frame(**model_kw)

  def report(self, params=None, torque=None):
    return self.diag.report(params or FakeParams(), torque, self.now)


class FakeParams:
  def __init__(self, data=None):
    self.data = data or {}

  def get(self, key):
    return self.data.get(key)

  def get_bool(self, key):
    return bool(self.data.get(key))


def event_bytes(service, **fields):
  msg = messaging.new_message(service)
  sub = getattr(msg, service)
  for k, v in fields.items():
    setattr(sub, k, v)
  return msg.to_bytes()


def cp_bytes(**kw):
  return car.CarParams.new_message(**kw).to_bytes()


class TestCamera:
  def test_none_without_calibration(self):
    assert Drive().report()["camera"] is None

  def test_cache_when_no_live(self):
    params = FakeParams({
      "CalibrationParams": event_bytes("extrinsicsCalibration", rpyCalib=[0.0, math.radians(2.0), math.radians(-1.5)],
                                       calStatus="calibrated", calPerc=100),
      "CameraOffset": 0.04,
    })
    cam = Drive().report(params)["camera"]
    assert cam["source"] == "cache"
    assert cam["calStatus"] == "calibrated"
    assert cam["calPerc"] == 100
    assert cam["pitchDeg"] == pytest.approx(2.0, abs=1e-3)
    assert cam["yawDeg"] == pytest.approx(-1.5, abs=1e-3)
    assert cam["cameraOffsetM"] == pytest.approx(0.04)
    lim = cam["limits"]
    assert lim["yawDeg"] == pytest.approx(3.96, abs=0.01)
    assert lim["pitchUpDeg"] > 0 and lim["pitchDownDeg"] > lim["pitchUpDeg"]

  def test_live_preferred_then_stale_falls_back(self):
    d = Drive()
    d.sm.msgs["extrinsicsCalibration"] = NS(rpyCalib=[0.0, 0.0, math.radians(3.0)], calStatus="recalibrating", calPerc=40)
    d.sm.updated["extrinsicsCalibration"] = True
    d.diag.update(d.sm, d.now)
    params = FakeParams({"CalibrationParams": event_bytes("extrinsicsCalibration", rpyCalib=[0.0, 0.0, 0.0],
                                                          calStatus="calibrated", calPerc=100)})
    live = d.report(params)["camera"]
    assert live["source"] == "live" and live["yawDeg"] == pytest.approx(3.0, abs=1e-3)
    assert live["calStatus"] == "recalibrating"
    d.now += 10.0
    assert d.report(params)["camera"]["source"] == "cache"


class TestDrift:
  def test_car_and_plan_offset_signs(self):
    d = Drive()
    # 车道中心在 y=-0.2（左边 0.2 m）→ 车偏右 0.2；规划 y=0 → 规划相对中心也偏右 0.2
    d.frames(200, left=-2.0, right=1.6, plan_y=0.0)
    drift = d.report()["drift"]
    assert drift["seconds"] == pytest.approx(10.0, abs=0.1)
    assert drift["carOffsetM"] == pytest.approx(0.2, abs=1e-3)
    assert drift["planOffsetM"] == pytest.approx(0.2, abs=1e-3)

  def test_plan_back_to_center(self):
    d = Drive()
    d.frames(100, left=-2.0, right=1.6, plan_y=-0.2)
    drift = d.report()["drift"]
    assert drift["carOffsetM"] == pytest.approx(0.2, abs=1e-3)
    assert drift["planOffsetM"] == pytest.approx(0.0, abs=1e-3)

  @pytest.mark.parametrize("name,kw", [
    ("carState", {"leftBlinker": True}),
    ("carState", {"steeringPressed": True}),
    ("carState", {"vEgo": 10.0}),
    ("carControl", {"latActive": False}),
    ("controlsState", {"desiredCurvature": 0.01}),
  ])
  def test_gating(self, name, kw):
    d = Drive()
    d.sm.msgs[name] = NS(**{**vars(d.sm.msgs[name]), **kw})
    d.frames(100)
    assert d.report()["drift"]["seconds"] == 0
    assert d.report()["drift"]["carOffsetM"] is None

  def test_gating_lane_quality(self):
    d = Drive()
    d.frames(50, probs=0.5)
    d.frames(50, left=-0.8, right=0.8)  # 车道宽 1.6 m 不合理
    assert d.report()["drift"]["seconds"] == 0

  def test_learned_params_live_and_cache(self):
    d = Drive()
    params = FakeParams({"LiveParametersV2": event_bytes("vehicleParameters", angleOffsetAverageDeg=1.5,
                                                         angleOffsetAverageValid=True)})
    drift = d.report(params, torque={"latAccelOffset": 0.12})["drift"]
    assert drift["angleOffsetDeg"] == pytest.approx(1.5)
    assert drift["angleOffsetValid"] is True
    assert drift["latAccelOffset"] == pytest.approx(0.12)
    d.sm.msgs["vehicleParameters"] = NS(angleOffsetAverageDeg=-3.0, angleOffsetAverageValid=False)
    d.sm.updated["vehicleParameters"] = True
    d.diag.update(d.sm, d.now)
    drift = d.report(params)["drift"]
    assert drift["angleOffsetDeg"] == pytest.approx(-3.0)
    assert drift["angleOffsetValid"] is False
    assert drift["latAccelOffset"] is None

  def test_accurate_angle(self):
    toyota = FakeParams({"CarParamsPersistent": cp_bytes(brand="toyota")})
    d = Drive()
    assert d.report(toyota)["drift"]["accurateAngle"] == "unknown"
    d.frames(5)
    assert d.report(toyota)["drift"]["accurateAngle"] == "pending"
    d.sm.msgs["carState"].steeringAngleOffsetDeg = 12.5
    d.frames(1)
    assert d.report(toyota)["drift"]["accurateAngle"] == "ready"
    honda = FakeParams({"CarParamsPersistent": cp_bytes(brand="honda")})
    assert d.report(honda)["drift"]["accurateAngle"] == "n/a"


def sine_frames(d, seconds, amp, period, center=0.0):
  for i in range(int(seconds / DT_MDL)):
    off = center + amp * math.sin(2 * math.pi * (i * DT_MDL) / period)
    # carOffset = -(left+right)/2 → 车偏右 off：车道中心 = -off
    d.frame(left=-1.8 - off, right=1.8 - off, plan_y=0.0)


class TestWeave:
  def test_weaving_window(self):
    d = Drive()
    sine_frames(d, 10.5, amp=0.25, period=4.0)
    w = d.report()["weave"]
    assert w["windows"] == 1
    assert w["weaveWindows"] == 1
    assert w["medianP2pM"] == pytest.approx(0.5, abs=0.06)
    assert w["medianPeriodS"] == pytest.approx(4.0, abs=0.5)

  def test_steady_offset_is_not_weaving(self):
    d = Drive()
    sine_frames(d, 21, amp=0.03, period=4.0, center=0.3)
    w = d.report()["weave"]
    assert w["windows"] == 2
    assert w["weaveWindows"] == 0

  def test_gap_restarts_window(self):
    d = Drive()
    sine_frames(d, 6, amp=0.25, period=4.0)
    d.sm.msgs["carState"].leftBlinker = True
    d.frames(5)
    d.sm.msgs["carState"].leftBlinker = False
    sine_frames(d, 6, amp=0.25, period=4.0)
    assert d.report()["weave"]["windows"] == 0

  def test_trace(self):
    d = Drive()
    d.frames(40, left=-2.0, right=1.6)  # 2 s 合格 → 8 点 0.2
    d.sm.msgs["carControl"].latActive = False
    d.frames(40)  # 2 s 不合格 → 8 点 None
    trace = d.report()["weave"]["trace"]
    assert len(trace) == 120
    assert trace[-1] is None
    assert any(v is not None and v == pytest.approx(0.2, abs=1e-3) for v in trace)
    assert trace[:100] == [None] * 100

  def test_delays(self):
    params = FakeParams({"CarParamsPersistent": cp_bytes(steerActuatorDelay=0.12),
                         "LiveDelay": event_bytes("lateralDelay", lateralDelay=0.3)})
    w = Drive().report(params)["weave"]
    assert w["factoryDelayS"] == pytest.approx(0.12)
    assert w["lateralDelayS"] == pytest.approx(0.3)


def controls(d, n, *, curvature=0.0, desired=0.0, saturated=False, output=0.0, kind="torqueState", v=20.0):
  d.sm.msgs["carState"].vEgo = v
  d.sm.msgs["controlsState"] = NS(desiredCurvature=desired, curvature=curvature,
                                  lateralControlState=LateralControlState(kind, saturated, output))
  for _ in range(n):
    d.now += 0.01
    d.sm.updated["controlsState"] = True
    d.sm.updated["carState"] = True
    d.diag.update(d.sm, d.now)
    d._clear()


class TestSteering:
  def test_saturation_in_curves(self):
    d = Drive()
    controls(d, 1000, desired=0.0, output=0.1)                         # 10 s 直道
    controls(d, 500, desired=0.005, curvature=0.004, output=0.9)       # 5 s 弯道 2.0 m/s²
    controls(d, 500, desired=0.005, curvature=0.004, saturated=True, output=1.0)  # 5 s 弯道饱和
    s = d.report()["steering"]
    assert s["control"] == "torque"
    assert s["activeSeconds"] == pytest.approx(20.0, abs=0.05)
    assert s["curveSeconds"] == pytest.approx(10.0, abs=0.05)
    assert s["saturatedCurveSeconds"] == pytest.approx(5.0, abs=0.05)
    assert s["usage"] == pytest.approx([10.0, 0, 0, 0, 10.0], abs=0.05)
    assert s["maxLatAccel"] == pytest.approx(0.004 * 400, abs=1e-3)

  def test_usage_bin_edges(self):
    d = Drive()
    controls(d, 100, output=0.6)  # 0.6 / 0.2 浮点除法会得到 2.999…，必须落在第 4 档 [.6,.8)
    controls(d, 100, output=-1.0)
    assert d.report()["steering"]["usage"] == pytest.approx([0, 0, 0, 1.0, 1.0], abs=0.02)

  def test_permanent_fault_counts_at_standstill(self):
    d = Drive()
    d.sm.msgs["carState"].steerFaultPermanent = True
    controls(d, 1, v=0.0)
    assert d.report()["steering"]["epsPermanent"] is True

  def test_angle_control_has_no_usage(self):
    d = Drive()
    controls(d, 100, kind="angleState", desired=0.005, saturated=True)
    s = d.report()["steering"]
    assert s["control"] == "angle"
    assert s["usage"] is None
    assert s["saturatedCurveSeconds"] == pytest.approx(1.0, abs=0.05)

  def test_inactive_or_slow_not_counted(self):
    d = Drive()
    controls(d, 100, v=3.0)
    d.sm.msgs["carControl"].latActive = False
    controls(d, 100)
    assert d.report()["steering"]["activeSeconds"] == 0

  def test_eps_fault_rising_edges_when_enabled(self):
    d = Drive()
    cs = d.sm.msgs["carState"]
    for fault in (False, True, True, False, True, False):
      cs.steerFaultTemporary = fault
      controls(d, 1)
    d.sm.msgs["carControl"].enabled = False
    cs.steerFaultTemporary = True
    controls(d, 1)
    s = d.report()["steering"]
    assert s["epsTempFaults"] == 2
    assert s["epsPermanent"] is False
    cs.steerFaultPermanent = True
    controls(d, 1)
    assert d.report()["steering"]["epsPermanent"] is True


class TestDriveLifecycle:
  def test_new_drive_resets_stats(self):
    d = Drive()
    d.frames(100)
    controls(d, 100)
    d.sm.msgs["deviceState"] = NS(started=False)
    d.sm.updated["deviceState"] = True
    d.now += 1.0
    d.diag.update(d.sm, d.now)
    parked = d.report()
    assert parked["drive"]["started"] is False
    assert parked["drift"]["seconds"] > 0  # 停车后保留上次
    seconds = parked["drive"]["seconds"]
    d.now += 60.0
    assert d.report()["drive"]["seconds"] == pytest.approx(seconds)
    d.sm.msgs["deviceState"] = NS(started=True)
    d.sm.updated["deviceState"] = True
    d.diag.update(d.sm, d.now)
    fresh = d.report()
    assert fresh["drive"]["started"] is True
    assert fresh["drift"]["seconds"] == 0
    assert fresh["steering"]["activeSeconds"] == 0
    assert fresh["weave"]["trace"] == [None] * 120

  def test_never_driven(self):
    diag = Diagnostics()
    r = diag.report(FakeParams(), None, 5.0)
    assert r["drive"] == {"started": False, "seconds": 0}
