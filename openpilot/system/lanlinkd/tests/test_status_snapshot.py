from types import SimpleNamespace as NS

from openpilot.system.lanlinkd.status_snapshot import build_capabilities, build_snapshot


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
  def test_none_cp_defaults(self):
    caps = build_capabilities(None, NS(), device_type="pc")
    assert caps["protocol_version"] == 1
    assert caps["device_type"] == "pc"
    assert caps["has_longitudinal_control"] is False
    assert caps["stock_longitudinal"] is True
    assert caps["is_development"] is True
    assert len(caps) == 19  # 对齐上游字段数

  def test_device_type_mici(self):
    # 设备上由 HARDWARE.get_device_type() 传入真实硬件标识
    caps = build_capabilities(None, NS(), device_type="mici")
    assert caps["device_type"] == "mici"

  def test_longitudinal_control_only_from_openpilot_long(self):
    # F4：openpilotLongitudinalControl 才表示 openpilot 控纵向；
    # lean-master/Sienna lateral-only 下 stock CP 的 hasLongitudinalControl=True 但 openpilot 未接管
    CP = NS(hasLongitudinalControl=True, openpilotLongitudinalControl=False)
    caps = build_capabilities(CP, NS(), device_type="mici")
    assert caps["has_longitudinal_control"] is False
    assert caps["stock_longitudinal"] is True

    CP = NS(openpilotLongitudinalControl=True)
    caps = build_capabilities(CP, NS(), device_type="mici")
    assert caps["has_longitudinal_control"] is True
    assert caps["stock_longitudinal"] is False

  def test_steer_control_type_enum_normalized(self):
    class FakeEnum:
      def __init__(self, raw):
        self.raw = raw

    CP = NS(steerControlType=FakeEnum(3))
    caps = build_capabilities(CP, NS(), device_type="mici")
    assert caps["steer_control_type"] == 3
