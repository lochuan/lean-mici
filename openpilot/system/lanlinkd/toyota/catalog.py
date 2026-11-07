"""懒加载 Sienna 4 代目录 data/sienna_catalog.json（Carista 数据源，tools/lanlink/build_toyota_sienna_catalog.py 生成；另有 Techstream 第二数据源，由 tools/lanlink/merge_sienna_techstream_items.py 叠加，未上车验证）。"""
import json
import os
from functools import cache

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sienna_catalog.json")


@cache
def load_sienna() -> dict:
  with open(CATALOG_PATH) as f:
    return json.load(f)


@cache
def _by_key(section: str, key_field: str) -> dict[str, dict]:
  return {entry[key_field]: entry for entry in load_sienna()[section]}


def ecu(ecu_id: str) -> dict | None:
  return _by_key("ecus", "id").get(ecu_id)


def setting(key: str) -> dict | None:
  return _by_key("settings", "key").get(key)


def live_item(key: str) -> dict | None:
  return _by_key("live", "key").get(key)


def option_label(options: list[dict] | None, value) -> str | None:
  """选项型取值的标签；不在选项里（或不是选项型）返回 None。"""
  return next((o["label"] for o in options or [] if o["value"] == value), None)


def ecu_address(ecu: dict) -> tuple[int, int | None]:
  """(请求 ID, 0x750 网关子地址或 None)。"""
  return int(ecu["tx"], 16), int(ecu["sub"], 16) if ecu["sub"] else None
