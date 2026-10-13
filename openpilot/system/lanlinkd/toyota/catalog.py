"""懒加载 data/*.json（由 Car Scanner APK 的定制项/ECU 表/故障码描述/PID 配置转换而来）。"""
import json
import os
from functools import cache

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LANG = "zh"


@cache
def load(name: str):
  with open(os.path.join(DATA_DIR, f"{name}.json")) as f:
    return json.load(f)


def text(value: dict) -> str:
  return value.get(LANG) or value.get("en", "")


def address_key(tx: str, sub: str | None) -> str:
  return f"{tx}/{sub}" if sub else tx


def ecu_name(tx: str, sub: str | None) -> str:
  ecus = load("ecus")
  table = ecus["gateway"] if sub else ecus["direct"]
  name = next((text(e["name"]) for e in table if e["tx"] == tx and e.get("sub") == sub), "")
  return f"{address_key(tx, sub)} {name}".strip()


def dtc_targets() -> list[dict]:
  """故障码扫描的地址表：直连 ECU + 0x750 网关子地址，按地址去重（Car Scanner 表里有重复子地址）。
  不扫直连 0x750：网关会把不带子地址的帧首字节当成子地址。"""
  ecus = load("ecus")
  targets: dict[str, dict] = {}
  for e in ecus["direct"] + ecus["gateway"]:
    key = address_key(e["tx"], e.get("sub"))
    if key != "750":
      targets.setdefault(key, {"ecu": key, "tx": int(e["tx"], 16), "sub": int(e["sub"], 16) if e.get("sub") else None,
                               "name": text(e["name"])})
  return list(targets.values())


def customization_items() -> list[dict]:
  return load("customizations")["items"]


def group_title(group: str) -> str:
  return text(load("customizations")["groups"].get(group, {"en": group}))


def pid_profiles() -> list[dict]:
  return load("pid_profiles")
