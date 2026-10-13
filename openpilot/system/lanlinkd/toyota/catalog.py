"""懒加载 data/*.json（由 Car Scanner APK 转换，见 .scratch/toyota-diag/spec.md）。"""
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


def ecu_name(tx: str, sub: str | None) -> str:
  ecus = load("ecus")
  table = ecus["gateway"] if sub else ecus["direct"]
  name = next((text(e["name"]) for e in table if e["tx"] == tx and e.get("sub") == sub), "")
  address = f"{tx}/{sub}" if sub else tx
  return f"{address} {name}".strip()


def customization_items() -> list[dict]:
  return load("customizations")["items"]


def customization_item(uid: int) -> dict | None:
  return next((i for i in customization_items() if i["uid"] == uid), None)


def group_title(group: str) -> str:
  return text(load("customizations")["groups"].get(group, {"en": group}))
