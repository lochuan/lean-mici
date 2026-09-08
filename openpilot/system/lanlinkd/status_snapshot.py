"""状态快照与设备能力的纯转换函数。services 为 name -> 消息对象（duck-type）。"""


def _get(obj, path: str, default):
  cur = obj
  for part in path.split("."):
    cur = getattr(cur, part, None)
    if cur is None:
      return default
  return cur


def _list(value) -> list:
  try:
    return list(value)
  except TypeError:
    return []


def _enum(v) -> int:
  """capnp enum 值归一为 int：动态代理带 .raw，Mock/裸 int 直接转。"""
  try:
    if hasattr(v, "raw"):
      return int(v.raw)
    return int(v)
  except (TypeError, ValueError):
    return 0


def build_snapshot(services: dict, version_info: dict, capabilities: dict) -> dict:
  ds = services.get("deviceState")
  cs = services.get("carState")
  psl = services.get("pandaStates")
  ps = psl[0] if psl else None
  gps = services.get("gpsLocation")
  return {
    "stale": not services,
    "device": {
      "cpuTempC": _list(_get(ds, "cpuTempC", [])),
      "gpuTempC": _list(_get(ds, "gpuTempC", [])),
      "memoryTempC": _get(ds, "memoryTempC", 0.0),
      "memoryUsagePercent": _get(ds, "memoryUsagePercent", 0),
      "freeSpacePercent": _get(ds, "freeSpacePercent", 0.0),
      "usbOnline": bool(_get(ds, "usbOnline", False)),
      "networkType": _enum(_get(ds, "networkType", 0)),
      "networkStrength": _enum(_get(ds, "networkStrength", 0)),
      "thermalStatus": _enum(_get(ds, "thermalStatus", 0)),
    },
    "car": {
      "vEgo": _get(cs, "vEgo", 0.0),
      "standstill": bool(_get(cs, "standstill", False)),
      "gearShifter": _enum(_get(cs, "gearShifter", 0)),
      "steeringAngleDeg": _get(cs, "steeringAngleDeg", 0.0),
      "gas": _get(cs, "gas", 0.0),
      "brake": _get(cs, "brake", 0.0),
      "leftBlinker": bool(_get(cs, "leftBlinker", False)),
      "rightBlinker": bool(_get(cs, "rightBlinker", False)),
      "leftBlindspot": bool(_get(cs, "leftBlindspot", False)),
      "rightBlindspot": bool(_get(cs, "rightBlindspot", False)),
      "fuelGauge": _get(cs, "fuelGauge", 0.0),
      "batteryPercent": _get(cs, "batteryPercent", 0),
    },
    "system": {
      "version": version_info.get("Version", ""),
      "branch": version_info.get("GitBranch", ""),
      "commit": version_info.get("GitCommit", "")[:8],
      "ignition": bool(_get(ps, "ignitionLine", False)),
    },
    "gps": {
      "latitude": _get(gps, "latitude", 0.0),
      "longitude": _get(gps, "longitude", 0.0),
      "altitude": _get(gps, "altitude", 0.0),
      "speed": _get(gps, "speed", 0.0),
      "satelliteCount": _get(gps, "satelliteCount", 0),
    },
    "capabilities": capabilities,
  }


_CAP_KEYS = (
  "protocol_version", "has_longitudinal_control", "has_icbm", "icbm_available",
  "torque_allowed", "brand", "pcm_cruise", "alpha_long_available",
  "steer_control_type", "enable_bsm", "is_release", "is_sp_release",
  "is_development", "tesla_has_vehicle_bus", "has_stop_and_go", "stock_longitudinal",
  "device_type", "subaru_has_sng", "hyundai_alpha_long_available")


def build_capabilities(CP, params, device_type: str) -> dict:
  """对齐上游 sunnypilot/sunnylink/capabilities.py 的 19 字段；无法映射的置默认。"""
  def cp(path, default=False):
    return bool(_get(CP, path, default))

  has_long = cp("openpilotLongitudinalControl")
  caps = {
    "protocol_version": 1,
    "has_longitudinal_control": has_long,
    "has_icbm": False,
    "icbm_available": False,
    "torque_allowed": False,
    "brand": _get(CP, "brand", "") or "",
    "pcm_cruise": cp("pcmCruise"),
    "alpha_long_available": False,
    "steer_control_type": _enum(_get(CP, "steerControlType", 0)),
    "enable_bsm": cp("enableBsm"),
    "is_release": False,
    "is_sp_release": False,
    "is_development": True,
    "tesla_has_vehicle_bus": False,
    "has_stop_and_go": False,
    "stock_longitudinal": not has_long,
    "device_type": device_type,
    "subaru_has_sng": False,
    "hyundai_alpha_long_available": False,
  }
  assert set(caps) == set(_CAP_KEYS)
  return caps
