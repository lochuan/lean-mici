"""车辆指纹 / 平台选择的纯逻辑（对应 sunnylink 的 Vehicle 页）。

两种状态：
  - **自动识别**：车辆被自动指纹识别，CarPlatformBundle 为空，平台来自
    CarParamsPersistent.carFingerprint。
  - **手动指定**：用户选过车型，CarPlatformBundle = car_list.json 的条目
    + {"name": <显示名>}（结构见 sunnypilot/system/params_migration.py）。

不在模块级 import cereal/opendbc：它们很重，且 lanlinkd 在无车环境
（开发机、测试）也要能起来。解析失败一律降级成"未识别"，不抛异常——
Vehicle 页打不开比显示"未识别"糟糕得多。
"""
import json
import os

CAR_LIST_PATH = os.path.join(
  os.path.dirname(os.path.abspath(__file__)),
  "../../sunnypilot/selfdrive/car/car_list.json",
)

BUNDLE_KEY = "CarPlatformBundle"
CP_KEY = "CarParamsPersistent"


def _decode_car_params(raw) -> dict:
  """从 CarParamsPersistent 取出展示所需字段。失败返回 {}。"""
  if raw is None:
    return {}
  try:
    from openpilot.cereal import messaging  # lazy：避免模块级重 import
    from opendbc.car.structs import car

    CP = messaging.log_from_bytes(bytes(raw), car.CarParams)
    return {
      "platform": str(CP.carFingerprint or ""),
      "brand": str(CP.brand or ""),
      "vin": str(CP.carVin or ""),
      "steer_control_type": str(CP.steerControlType),
      "pcm_cruise": bool(CP.pcmCruise),
      "openpilot_longitudinal": bool(CP.openpilotLongitudinalControl),
      "alpha_long_available": bool(CP.alphaLongitudinalAvailable),
      "enable_bsm": bool(CP.enableBsm),
      "radar_unavailable": bool(CP.radarUnavailable),
      "mass_kg": round(float(CP.mass), 1),
      "wheelbase_m": round(float(CP.wheelbase), 2),
    }
  except Exception:
    # 解析不了就当没有指纹信息；调用方会显示"未识别"
    return {}


def load_car_list(path: str = CAR_LIST_PATH) -> dict:
  """可选车型表。文件缺失时返回 {}（车机上一定有，开发机上可能没有）。"""
  try:
    with open(os.path.normpath(path)) as f:
      data = json.load(f)
    return data if isinstance(data, dict) else {}
  except (OSError, ValueError):
    return {}


def vehicle_state(store, car_list: dict | None = None) -> dict:
  cars = load_car_list() if car_list is None else car_list

  bundle = store.get(BUNDLE_KEY)
  bundle = bundle if isinstance(bundle, dict) and bundle else None

  detected = _decode_car_params(store.get(CP_KEY))

  # 手动指定优先：它是用户的显式选择，而 CarParamsPersistent 可能还是上一次
  # 点火时自动识别的结果（改完车型要下次上电才生效）。
  if bundle:
    name = str(bundle.get("name") or "")
    platform = str(bundle.get("platform") or "")
    brand = str(bundle.get("brand") or detected.get("brand", ""))
    source = "manual"
  else:
    name = ""
    platform = detected.get("platform", "")
    brand = detected.get("brand", "")
    source = "auto" if platform else "none"
    # 自动识别时用 car_list 反查一个人类可读名
    if platform:
      name = next((k for k, v in cars.items() if v.get("platform") == platform), "")

  return {
    "source": source,              # manual | auto | none
    "name": name,                  # 显示名，如 "Toyota Sienna 2021-23"
    "platform": platform,          # 如 "TOYOTA_SIENNA_PATCHED"
    "brand": brand,
    "fingerprinted": bool(platform),
    "detected": detected,          # 指纹推导出的能力（只读展示）
    # 只发名字列表，不发整张表：72 条 × 完整字段没必要占带宽，
    # 选择时前端只需要 name。
    "choices": sorted(cars.keys()),
  }


def select_platform(store, name: str, car_list: dict | None = None) -> tuple[int, str]:
  """按显示名写入 CarPlatformBundle。空名 = 清除，回到自动识别。"""
  cars = load_car_list() if car_list is None else car_list

  if not name:
    store.remove(BUNDLE_KEY)
    return 204, ""

  entry = cars.get(name)
  if entry is None:
    return 404, "unknown vehicle"

  # 结构必须与 params_migration._migrate_car_platform_bundle 写入的一致，
  # 否则下次迁移会读不到 platform。
  store.put(BUNDLE_KEY, {**entry, "name": name}, block=True)
  return 204, ""
