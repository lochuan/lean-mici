import importlib.util
import json
import os

import pytest

from openpilot.system.lanlinkd.toyota import catalog

_TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "tools", "lanlink")
_SCRIPT = os.path.join(_TOOLS, "merge_sienna_techstream_items.py")
_ITEMS_PATH = os.path.join(_TOOLS, "sienna_techstream_items.json")
if not os.path.exists(_SCRIPT):
  pytest.skip("设备上的精简发布树没有 tools/", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("merge_sienna_techstream_items", _SCRIPT)
merge_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge_script)


def _live(key: str, name: str = "x", **extra) -> dict:
  return {"key": key, "name": name, "ecu": "700", "did": "0001", "byteIndex": 0, "mask": "FF", "mult": 1.0, "offset": 0.0, "signed": False,
          "precision": 0, "unit": "", "options": None, "needsEngineRunning": False, **extra}


def test_shipped_catalog_is_the_merge_of_shipped_items():
  # 目录原文再合并一次必须逐字节不变：同时证明幂等、输出格式、数据文件与目录没有漂移
  with open(catalog.CATALOG_PATH) as f:
    catalog_text = f.read()
  with open(_ITEMS_PATH) as f:
    items = json.load(f)
  merged = json.loads(catalog_text)
  merge_script.merge(items, merged)
  assert json.dumps(merged, ensure_ascii=False, indent=1) + "\n" == catalog_text


def test_merge_appends_overwrites_drops_source_and_leaves_settings(tmp_path):
  catalog_before = {
    "ecus": [{"id": "700", "name": "发动机"}],
    "live": [_live("old", "旧项"), _live("shared", "旧名")],
    "liveTools": [{"id": "engine", "keys": ["old"]}],
    "settings": [{"key": "untouched"}],
  }
  items = {
    "origin": "测试",
    "ecus": [{"id": "700", "name": "发动机（新）"}, {"id": "750/5F", "name": "中央网关"}],
    "live": [_live("shared", "新名", source={"ref_did": "0001"}), _live("fresh", "新项", source={"ref_did": "0002"})],
    "liveTools": [{"id": "engine", "keys": ["old", "fresh"]}, {"id": "tpms", "keys": ["fresh"]}],
  }
  items_path, catalog_path = tmp_path / "items.json", tmp_path / "catalog.json"
  items_path.write_text(json.dumps(items))
  catalog_path.write_text(json.dumps(catalog_before))

  merge_script.main(str(items_path), str(catalog_path))

  merged = json.loads(catalog_path.read_text())
  assert merged["ecus"] == [{"id": "700", "name": "发动机（新）"}, {"id": "750/5F", "name": "中央网关"}]
  assert [(i["key"], i["name"]) for i in merged["live"]] == [("old", "旧项"), ("shared", "新名"), ("fresh", "新项")]
  assert merged["liveTools"] == [{"id": "engine", "keys": ["old", "fresh"]}, {"id": "tpms", "keys": ["fresh"]}]
  assert merged["settings"] == [{"key": "untouched"}]
  assert all(list(i) == merge_script.LIVE_KEYS for i in merged["live"])
  assert "source" not in catalog_path.read_text()
