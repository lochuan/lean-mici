"""状态快照与设备能力的纯转换函数。services/params/消息对象均为 duck-type。"""
from openpilot.cereal import messaging, custom
from opendbc.car.structs import car
from openpilot.common.swaglog import cloudlog


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
      "started": bool(_get(ds, "started", False)),
      "cpuTempC": _list(_get(ds, "cpuTempC", [])),
      "gpuTempC": _list(_get(ds, "gpuTempC", [])),
      "memoryTempC": _get(ds, "memoryTempC", 0.0),
      "maxTempC": _get(ds, "maxTempC", 0.0),
      "memoryUsagePercent": _get(ds, "memoryUsagePercent", 0),
      "cpuUsagePercent": _list(_get(ds, "cpuUsagePercent", [])),
      "gpuUsagePercent": _get(ds, "gpuUsagePercent", 0),
      "freeSpacePercent": _get(ds, "freeSpacePercent", 0.0),
      "powerDrawW": _get(ds, "powerDrawW", 0.0),
      "fanSpeedPercentDesired": _get(ds, "fanSpeedPercentDesired", 0),
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


def build_capabilities(params, device_type: str) -> dict:
  """对齐上游 sunnypilot/sunnylink/capabilities.py::generate_capabilities 的 19 字段。
  params: duck-type（.get/.get_bool）。从持久化 params 读取（熄火停车立即可用），
  不依赖实时 SubMaster carParams。反序列化失败置默认不抛异常。
  注：lean fork 的 opendbc 仅 Toyota，无 hyundai/subaru/tesla 目录，
  故 tesla_has_vehicle_bus/subaru_has_sng/hyundai_alpha_long_available 恒 False
  （settings_ui.json 中对应品牌 vehicle_settings 永不显示，行为一致）。"""
  caps = {k: False for k in _CAP_KEYS}
  caps.update({"protocol_version": 1, "brand": "", "steer_control_type": "",
               "device_type": device_type})

  def bool_param(key: str) -> bool:
    try:
      return bool(params.get_bool(key))
    except Exception:
      return False

  # 硬件无关 bool params（对齐上游）
  caps["is_release"] = False  # 上游注释：恒 False
  caps["is_sp_release"] = bool_param("IsReleaseSpBranch")
  caps["is_development"] = bool_param("IsDevelopmentBranch")
  caps["stock_longitudinal"] = bool_param("ToyotaEnforceStockLongitudinal")

  # CarPlatformBundle（JSON dict）优先定 brand；CP 兜底
  bundle = params.get("CarPlatformBundle") if params is not None else None
  bundle_brand = bundle.get("brand", "") if isinstance(bundle, dict) else ""
  if bundle_brand:
    caps["brand"] = bundle_brand

  CP = None
  CP_bytes = params.get("CarParamsPersistent") if params is not None else None
  if CP_bytes is not None:
    try:
      CP = messaging.log_from_bytes(bytes(CP_bytes), car.CarParams)
      caps["alpha_long_available"] = bool(CP.alphaLongitudinalAvailable)
      if CP.alphaLongitudinalAvailable:
        caps["has_longitudinal_control"] = bool_param("AlphaLongitudinalEnabled")
      else:
        caps["has_longitudinal_control"] = bool(CP.openpilotLongitudinalControl)
      # CP.steerControlType 是物理控制方式；发枚举名字符串（"torque"/"angle"/...），
      # 与 settings_ui.json 的 capability 规则（字符串比较）匹配
      caps["steer_control_type"] = str(CP.steerControlType)
      caps["torque_allowed"] = CP.steerControlType != car.CarParams.SteerControlType.angle
      if not caps["brand"] and CP.brand:
        caps["brand"] = str(CP.brand)
      caps["pcm_cruise"] = bool(CP.pcmCruise)
      caps["enable_bsm"] = bool(CP.enableBsm)
      # 通用 SnG 兜底（品牌规则本 fork 不适用）
      caps["has_stop_and_go"] = bool(CP.openpilotLongitudinalControl)
    except Exception:
      cloudlog.exception("lanlink capabilities: CarParamsPersistent 解析失败")

  CP_SP_bytes = params.get("CarParamsSPPersistent") if params is not None else None
  if CP_SP_bytes is not None:
    try:
      CP_SP = messaging.log_from_bytes(bytes(CP_SP_bytes), custom.CarParamsSP)
      caps["icbm_available"] = bool(CP_SP.intelligentCruiseButtonManagementAvailable)
      caps["has_icbm"] = caps["icbm_available"] and bool_param("IntelligentCruiseButtonManagement")
    except Exception:
      cloudlog.exception("lanlink capabilities: CarParamsSPPersistent 解析失败")

  assert set(caps) == set(_CAP_KEYS)
  return caps


_DL_ACTIVE = ("downloading", "verifying")
_DL_DONE = ("downloaded", "cached")


def _enum_name(v) -> str:
  try:
    n = getattr(v, "name", None) or str(v)
    return str(n).lower()
  except Exception:
    return "notdownloading"


def build_models_download(mm) -> dict | None:
  """modelManagerSP 消息 -> 下载状态卡数据（对齐车内 UI models.py 的进度算法：
  progress = Σ artifact 进度 / 数量，downloaded/cached 计 100；任一 verifying → verifying）。
  supercombo artifact 存在时以其 eta 为准。"""
  sel = getattr(mm, "selectedBundle", None)
  if sel is None:
    return None
  models = list(getattr(sel, "models", []) or [])
  if not models:
    return None
  progress = 0.0
  eta = 0
  verifying = False
  super_eta = None
  for m in models:
    dp = getattr(getattr(m, "artifact", None), "downloadProgress", None)
    st = _enum_name(getattr(dp, "status", "notDownloading")) if dp is not None else "notdownloading"
    pv = float(getattr(dp, "progress", 0.0) or 0.0) if dp is not None else 0.0
    ev = int(getattr(dp, "eta", 0) or 0) if dp is not None else 0
    if st in _DL_ACTIVE:
      verifying = verifying or (st == "verifying")
      progress += pv
      eta = max(eta, ev)
    elif st in _DL_DONE:
      progress += 100.0
    if _enum_name(getattr(m, "type", "supercombo")) == "supercombo" and dp is not None:
      super_eta = ev if st in _DL_ACTIVE else (0 if st in _DL_DONE else super_eta)
  return {
    "ref": getattr(sel, "ref", "") or "",
    "displayName": getattr(sel, "displayName", "") or "",
    "internalName": getattr(sel, "internalName", "") or "",
    "status": _enum_name(getattr(sel, "status", "notDownloading")),
    "verifying": verifying,
    "progress": round(progress / len(models), 2),
    "eta": super_eta if super_eta is not None else eta,
  }
