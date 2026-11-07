#!/usr/bin/env python3
"""生成 LANLink 丰田诊断的 Sienna 4 代目录 `toyota/data/sienna_catalog.json`。

只适配用户自己的 Sienna 4 代 HV（XL40）。输入不在仓库里：
- sienna_snapshot.json：Carista（libCarista.so）定义 × 实车只读探测的交集，
  由 `~/Documents/car_scanner/carista/probe/build_sienna_snapshot.py` 生成；
- sienna_zh.json：人工中文翻译，由 `carista/translations/build_sienna_zh.py` 生成。
「常用」8 项、分类图标、实时数据主题是人工选择，写在本脚本里（与用户认可的原型一致）。

生成后需再跑 merge_sienna_techstream_items.py 叠加 Techstream 的 ECU 与实时数据项（第二数据源）。

用法：
  tools/lanlink/build_toyota_sienna_catalog.py <sienna_snapshot.json> <sienna_zh.json> <输出 json>
"""
import json
import sys

# 两侧滑门脚踢感应灵敏度：实车读到 3，超出选项 0～2，不收
EXCLUDED_SETTINGS = {"car_setting_power_left_door_kick_sensor_sensitivity", "car_setting_power_right_door_kick_sensor_sensitivity"}

# lucide 图标名（kebab-case），前端按名字映射到组件
CATEGORY_ICONS = {
  "DINGS_AND_WARNINGS": "bell", "DRIVER_ASSIST": "shield-check", "HEATER_AC": "snowflake", "INSTR_DISPLAY_AND_NAV": "gauge",
  "LIGHTS_AUTOMATIC": "sun-moon", "LIGHTS_EXTERIOR_LIGHTS": "lightbulb", "LIGHTS_INTERIOR_LIGHTS": "lamp-ceiling",
  "LOCKING_AUTOMATIC": "lock", "LOCKING_BEEP_AND_BLINK": "megaphone", "LOCKING_DOORS_AND_ALARM": "siren",
  "LOCKING_SMART_KEY": "key-round", "MIRRORS": "flip-horizontal-2", "PARKING_SENSORS": "radar",
  "SEATS_AND_STEERING_WHEEL": "armchair", "SLIDING_DOORS": "door-open", "TRUNK": "package-open",
  "WINDOWS_AND_SUNROOF": "panel-top", "WIPERS_AND_WASHER": "cloud-rain",
}

# 车主最常改的 8 项（参考 Carista Most Popular）
POPULAR_SETTINGS = [
  "car_setting_comfort_turn_signal_count", "car_setting_coming_home_duration", "car_setting_auto_fold_mirrors",
  "car_setting_comfort_open_remote", "car_setting_left_sliding_door_via_remote_type",
  "car_setting_rear_hatch_open_angle", "car_setting_exterior_light_on_when_approached", "car_setting_auto_relock_time",
]

LIVE_TOOLS = [
  ("wheels", "车轮速度", "circle-gauge", ["car_tool_front_left_wheel_speed", "car_tool_front_right_wheel_speed",
                                          "car_tool_rear_left_wheel_speed", "car_tool_rear_right_wheel_speed"]),
  ("engine", "发动机与 12V", "battery-charging", ["car_tool_battery_voltage_engine", "car_tool_engine_speed", "car_calculated_load",
                                                 "car_tool_coolant_temp", "car_tool_mileage", "car_tool_mileage_unit"]),
  ("hvac", "空调", "thermometer", ["car_tool_compressor_speed", "car_tool_inside_temp"]),
]

# 发动机不转时读数没有意义（混动在 Ready 下发动机经常不转，实测转速 0）
ENGINE_RUNNING_LIVE_ITEMS = {"car_tool_engine_speed", "car_calculated_load", "car_tool_coolant_temp"}

LIVE_OPTION_LABELS = {"km": "公里", "miles": "英里"}

# 动力电池 12 块：块 1、2、11、12 是半块（2 模组），其余整块（4 模组）
BATTERY_BLOCKS = 12
HALF_BATTERY_BLOCKS = {1, 2, 11, 12}


def _hex_id(value: str, width: int) -> str:
  return f"{int(value, 16):0{width}X}"


def _ecu_id(tx: str, sub: str | None) -> str:
  return f"{_hex_id(tx, 3)}/{_hex_id(sub, 2)}" if sub else _hex_id(tx, 3)


def build_ecus(snapshot: dict, zh: dict) -> list[dict]:
  # 按探测时 A8 03 的应答定协议（Carista 同样按 ECU 判定）：7F A8 11 = 不认 Toyota CAN 命令 → UDS；
  # E8 03 正响应（车窗）和多帧应答错乱的 750/AD（天窗，对 19 也回 7F 19 11）→ Toyota CAN
  return [{"id": _ecu_id(e["tx"], e["sub"]), "tx": _hex_id(e["tx"], 3), "sub": _hex_id(e["sub"], 2) if e["sub"] else None,
           "name": zh["ecus"][e["ecu"]], "protocol": "uds" if e["a803"] == "7fa811" else "toyota_can"}
          for e in snapshot["ecus"]]


def build_settings(snapshot: dict, zh: dict) -> list[dict]:
  settings = []
  for s in snapshot["settings"]:
    if s["name_key"] in EXCLUDED_SETTINGS:
      continue
    translation = zh["settings"][s["name_key"]]
    interpretation = s["interpretation"]
    numeric = interpretation.get("type") == "numeric"
    settings.append({
      "key": s["name_key"], "category": s["category"].split("::")[1], "name": translation["name"],
      "ecu": _ecu_id(s["ecu"]["request_id"], s["ecu"]["sub_address"]),
      "did": _hex_id(s["did"], 4) if s["protocol"] == "UDS" else None,
      "localId": _hex_id(s["can_local_id"], 2) if s["protocol"] == "TOYOTA_CAN" else None,
      "byteIndex": s["byte_index"], "mask": s["mask"].upper(),
      "requiresBitSupportCheck": bool(s["requires_bit_support_check"]),
      "options": [] if numeric else [{"value": o["value"], "label": translation["options"][str(o["value"])]}
                                     for o in interpretation["options"]],
      "numeric": {"min": interpretation["min_raw"], "max": interpretation["max_raw"]} if numeric else None,
      "popular": s["name_key"] in POPULAR_SETTINGS,
    })
  return settings


def build_live(snapshot: dict, zh: dict) -> list[dict]:
  live = []
  for item in snapshot["live"]:
    interpretation = item["interpretation"]
    options = interpretation.get("options")
    live.append({
      "key": item["name_key"], "name": zh["live"][item["name_key"]],
      "ecu": _ecu_id(item["ecu"]["request_id"], item["ecu"]["sub_address"]), "did": _hex_id(item["did"], 4),
      "byteIndex": item["byte_index"], "mask": item["mask"].upper(),
      "mult": interpretation.get("mult", 1.0), "offset": interpretation.get("offset", 0.0),
      "signed": interpretation.get("numtype") == 2, "precision": interpretation.get("precision") or 0,
      "unit": zh["units"].get(interpretation.get("unit"), ""),
      "options": [{"value": o["value"], "label": LIVE_OPTION_LABELS[o["label"]]} for o in options] if options else None,
      "needsEngineRunning": item["name_key"] in ENGINE_RUNNING_LIVE_ITEMS,
    })
  return live


def build(snapshot: dict, zh: dict) -> dict:
  settings = build_settings(snapshot, zh)
  live = build_live(snapshot, zh)
  live_keys = {i["key"] for i in live}
  assert set(POPULAR_SETTINGS) <= {s["key"] for s in settings}, "常用项不在目录里"
  assert all(k in live_keys for _, _, _, keys in LIVE_TOOLS for k in keys), "实时数据主题引用了不存在的项"
  battery_key = "car_ev_or_hybrid_battery_n1_block_{n}_{kind}"
  return {
    "ecus": build_ecus(snapshot, zh),
    "categories": [{"id": c.split("::")[1], "name": name, "icon": CATEGORY_ICONS[c.split("::")[1]]} for c, name in zh["categories"].items()],
    "settings": settings,
    "live": live,
    "liveTools": [{"id": i, "name": name, "icon": icon, "keys": keys} for i, name, icon, keys in LIVE_TOOLS],
    "battery": {"ecu": "7D2", "blocks": [{"voltageKey": battery_key.format(n=n, kind="voltage"),
                                          "resistanceKey": battery_key.format(n=n, kind="internal_resistance"),
                                          "modules": 2 if n in HALF_BATTERY_BLOCKS else 4} for n in range(1, BATTERY_BLOCKS + 1)]},
    "dtcText": zh["dtc"],   # {codes: {基码: 说明}, ftb: {FTB: 说明}}，Carista 码表的中文翻译
  }


def main(snapshot_path: str, zh_path: str, out_path: str) -> None:
  with open(snapshot_path) as f:
    snapshot = json.load(f)
  with open(zh_path) as f:
    zh = json.load(f)
  catalog = build(snapshot, zh)
  with open(out_path, "w") as f:
    json.dump(catalog, f, ensure_ascii=False, indent=1)
    f.write("\n")
  print(f"{len(catalog['ecus'])} 个 ECU，{len(catalog['settings'])} 项定制，{len(catalog['live'])} 项实时数据 → {out_path}")


if __name__ == "__main__":
  if len(sys.argv) != 4:
    sys.exit(__doc__)
  main(*sys.argv[1:])
