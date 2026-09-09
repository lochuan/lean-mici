from types import SimpleNamespace as NS

from opendbc.car.structs import car
from openpilot.cereal import messaging, custom

from openpilot.system.lanlinkd.status_snapshot import build_capabilities, build_snapshot


def cp_bytes(**kw) -> bytes:
  return car.CarParams.new_message(**kw).to_bytes()


def cp_sp_bytes(**kw) -> bytes:
  return custom.CarParamsSP.new_message(**kw).to_bytes()


class FakeParams:
  """duck-type Params：get 返回 python 值（JSON->dict, BYTES->bytes），get_bool bool。"""

  def __init__(self, data=None, bools=None):
    self.data = data or {}
    self.bools = bools or {}

  def get(self, key):
    return self.data.get(key)

  def get_bool(self, key):
    return bool(self.bools.get(key, False))


def angle_cp_params():
  return FakeParams(data={"CarParamsPersistent": cp_bytes(
    steerControlType="angle", openpilotLongitudinalControl=True,
    brand="toyota", pcmCruise=True, enableBsm=True,
    alphaLongitudinalAvailable=False)})


class TestBuildSnapshot:
  def test_full_snapshot(self):
    services = {
      "deviceState": NS(cpuTempC=[41.0, 42.0], gpuTempC=[39.0], memoryTempC=40.0,
                        memoryUsagePercent=55, freeSpacePercent=70.0, usbOnline=True,
                        networkType=5, networkStrength=2, thermalStatus=0),
      "carState": NS(vEgo=12.3, gearShifter=3, steeringAngleDeg=4.5, gas=0.1, brake=0.0,
                     leftBlinker=False, rightBlinker=True, leftBlindspot=False,
                     rightBlindspot=True, fuelGauge=0.6, batteryPercent=80, standstill=False),
      "pandaStates": [NS(ignitionLine=True)],
      "gpsLocation": NS(latitude=31.2, longitude=121.5, altitude=4.0, speed=12.0,
                        satelliteCount=11),
    }
    snap = build_snapshot(services, {"Version": "0.11.2"}, {"brand": "toyota"})
    assert snap["device"]["cpuTempC"] == [41.0, 42.0]
    assert snap["device"]["memoryUsagePercent"] == 55
    assert snap["car"]["vEgo"] == 12.3
    assert snap["car"]["rightBlinker"] is True
    assert snap["system"]["version"] == "0.11.2"
    assert snap["system"]["ignition"] is True
    assert snap["gps"]["latitude"] == 31.2
    assert snap["gps"]["satelliteCount"] == 11
    assert snap["capabilities"] == {"brand": "toyota"}
    assert snap["stale"] is False

  def test_missing_services_yield_defaults(self):
    snap = build_snapshot({}, {}, {})
    assert snap["stale"] is True
    assert snap["device"]["cpuTempC"] == []
    assert snap["car"]["vEgo"] == 0.0
    assert snap["gps"]["satelliteCount"] == 0

  def test_capnp_enum_values_normalized_to_int(self):
    # 设备上 capnp enum 为动态代理对象，带 .raw；直接 JSON 序列化会 500
    class FakeEnum:
      def __init__(self, raw):
        self.raw = raw

    services = {
      "deviceState": NS(networkType=FakeEnum(5), networkStrength=FakeEnum(2), thermalStatus=FakeEnum(0)),
      "carState": NS(gearShifter=FakeEnum(3)),
      "gpsLocation": NS(satelliteCount=9),
    }
    snap = build_snapshot(services, {}, {})
    assert snap["device"]["networkType"] == 5
    assert snap["device"]["networkStrength"] == 2
    assert snap["device"]["thermalStatus"] == 0
    assert snap["car"]["gearShifter"] == 3

  def test_broken_enum_falls_back_to_zero(self):
    services = {"deviceState": NS(networkType=object(), thermalStatus=object()),
                "carState": NS(gearShifter=object())}
    snap = build_snapshot(services, {}, {})
    assert snap["device"]["networkType"] == 0
    assert snap["device"]["thermalStatus"] == 0
    assert snap["car"]["gearShifter"] == 0


class TestBuildCapabilities:
  def test_empty_params_all_defaults(self):
    caps = build_capabilities(FakeParams(), device_type="pc")
    assert caps["protocol_version"] == 1
    assert caps["device_type"] == "pc"
    assert caps["brand"] == ""
    assert caps["steer_control_type"] == ""
    assert caps["torque_allowed"] is False
    assert caps["has_longitudinal_control"] is False
    assert caps["stock_longitudinal"] is False
    assert caps["is_development"] is False
    assert len(caps) == 19

  def test_angle_steering_from_persistent_cp(self):
    caps = build_capabilities(angle_cp_params(), device_type="mici")
    assert caps["steer_control_type"] == "angle"
    assert caps["torque_allowed"] is False
    assert caps["brand"] == "toyota"
    assert caps["has_longitudinal_control"] is True
    assert caps["pcm_cruise"] is True
    assert caps["enable_bsm"] is True
    assert caps["has_stop_and_go"] is True
    assert caps["alpha_long_available"] is False

  def test_torque_steering_and_alpha_long(self):
    p = FakeParams(
      data={"CarParamsPersistent": cp_bytes(
        steerControlType="torque", alphaLongitudinalAvailable=True,
        openpilotLongitudinalControl=False)},
      bools={"AlphaLongitudinalEnabled": True})
    caps = build_capabilities(p, device_type="mici")
    assert caps["steer_control_type"] == "torque"
    assert caps["torque_allowed"] is True
    assert caps["alpha_long_available"] is True
    assert caps["has_longitudinal_control"] is True

  def test_alpha_available_false_uses_cp_long(self):
    p = FakeParams(data={"CarParamsPersistent": cp_bytes(
      steerControlType="torque", alphaLongitudinalAvailable=False,
      openpilotLongitudinalControl=True)})
    caps = build_capabilities(p, device_type="mici")
    assert caps["has_longitudinal_control"] is True

  def test_bundle_brand_wins_over_cp(self):
    p = FakeParams(data={
      "CarPlatformBundle": {"brand": "toyota", "platform": "TOYOTA_SIENNA_4TH_GEN"},
      "CarParamsPersistent": cp_bytes(steerControlType="angle", brand="hyundai")})
    caps = build_capabilities(p, device_type="mici")
    assert caps["brand"] == "toyota"

  def test_icbm_from_sp_params(self):
    p = FakeParams(
      data={"CarParamsSPPersistent": cp_sp_bytes(
        intelligentCruiseButtonManagementAvailable=True)},
      bools={"IntelligentCruiseButtonManagement": True})
    caps = build_capabilities(p, device_type="mici")
    assert caps["icbm_available"] is True
    assert caps["has_icbm"] is True

  def test_icbm_available_without_enabled(self):
    p = FakeParams(data={"CarParamsSPPersistent": cp_sp_bytes(
      intelligentCruiseButtonManagementAvailable=True)})
    caps = build_capabilities(p, device_type="mici")
    assert caps["icbm_available"] is True
    assert caps["has_icbm"] is False

  def test_lean_fork_brand_flags_stay_false(self):
    # lean fork opendbc 仅 Toyota：hyundai/subaru/tesla 专属能力恒 False
    caps = build_capabilities(FakeParams(), device_type="mici")
    assert caps["hyundai_alpha_long_available"] is False
    assert caps["subaru_has_sng"] is False
    assert caps["tesla_has_vehicle_bus"] is False

  def test_bool_params_flags(self):
    p = FakeParams(bools={"IsReleaseSpBranch": True, "ToyotaEnforceStockLongitudinal": True})
    caps = build_capabilities(p, device_type="mici")
    assert caps["is_sp_release"] is True
    assert caps["is_release"] is False
    assert caps["stock_longitudinal"] is True

  def test_corrupt_bytes_falls_back_to_defaults(self):
    p = FakeParams(data={"CarParamsPersistent": b"\x00garbage",
                         "CarParamsSPPersistent": b"\x00garbage"})
    caps = build_capabilities(p, device_type="mici")
    assert caps["steer_control_type"] == ""
    assert caps["brand"] == ""
    assert caps["icbm_available"] is False
