"""settings_ui.json 的缺失 key 标记（编译期校验的运行时等价物）。"""
import json


def _mark_item(item: dict, key_exists) -> None:
  key = item.get("key")
  if key and not key_exists(key):
    item["_missing"] = True
  # sub_items 是行内展开的子设置（如 BlinkerPauseLateralControl 的两个滑块），
  # 也要检查：漏标的话前端会当它可用，写入时才 404。
  for child in item.get("sub_items", []):
    _mark_item(child, key_exists)


def _walk_items(node: dict, key_exists) -> None:
  for item in node.get("items", []):
    _mark_item(item, key_exists)
  for sub in node.get("sub_panels", []):
    _walk_items(sub, key_exists)


def mark_missing_keys(settings_ui: dict, key_exists) -> dict:
  out = json.loads(json.dumps(settings_ui))  # deep copy，避免污染静态原数据
  for panel in out.get("panels", []):
    for section in panel.get("sections", []):
      _walk_items(section, key_exists)
  for brand in out.get("vehicle_settings", {}).values():
    _walk_items(brand, key_exists)
  return out
