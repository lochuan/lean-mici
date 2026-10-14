"""懒加载 data/*.json。

sienna_catalog.json：Sienna 4 代目录（Carista 数据源，tools/lanlink/build_toyota_sienna_catalog.py 生成）。
其余文件由 Car Scanner APK 转换而来，迁移完成后删除。
"""
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


def pid_profiles() -> list[dict]:
  return load("pid_profiles")


# ---- Sienna 4 代目录 ----

def load_sienna() -> dict:
  return load("sienna_catalog")


@cache
def _by_key(section: str, key_field: str) -> dict[str, dict]:
  return {entry[key_field]: entry for entry in load_sienna()[section]}


def ecu(ecu_id: str) -> dict | None:
  return _by_key("ecus", "id").get(ecu_id)


def setting(key: str) -> dict | None:
  return _by_key("settings", "key").get(key)


def live_item(key: str) -> dict | None:
  return _by_key("live", "key").get(key)
