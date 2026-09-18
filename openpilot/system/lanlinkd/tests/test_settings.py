import json
import os
import re

from openpilot.system.lanlinkd.settings import mark_missing_keys

SETTINGS = {
  "panels": [{
    "id": "steering",
    "label": "Steering",
    "sections": [{
      "title": "Lateral",
      "items": [
        {"key": "IsMetric", "widget": "toggle"},
        {"key": "GhostParam", "widget": "toggle"},
        {
          "key": "IsMetric",
          "widget": "toggle",
          # 行内子设置：真实 schema 里 BlinkerPauseLateralControl 用的就是这个结构
          "sub_items": [
            {"key": "IsMetric", "widget": "option"},
            {"key": "InlineGhost", "widget": "option"},
          ],
        },
      ],
      "sub_panels": [{
        "label": "Advanced",
        "items": [{"key": "SubGhost", "widget": "toggle"}],
      }],
    }],
  }],
  "vehicle_settings": {
    "toyota": {
      "title": "Toyota / Lexus Settings",
      "items": [{"key": "ToyotaGhost", "widget": "toggle"}],
    },
  },
}


def test_marks_missing_keys():
  out = mark_missing_keys(SETTINGS, lambda key: key == "IsMetric")
  items = out["panels"][0]["sections"][0]["items"]
  assert "_missing" not in items[0]
  assert items[1]["_missing"] is True
  sub_items = out["panels"][0]["sections"][0]["sub_panels"][0]["items"]
  assert sub_items[0]["_missing"] is True
  vehicle_items = out["vehicle_settings"]["toyota"]["items"]
  assert vehicle_items[0]["_missing"] is True


def test_marks_missing_keys_inside_sub_items():
  """行内 sub_items 也要标记。

  漏标的后果不是显示问题：前端会把它当成可用控件渲染出来，用户去调，
  写入时才收到 404。
  """
  out = mark_missing_keys(SETTINGS, lambda key: key == "IsMetric")
  sub_items = out["panels"][0]["sections"][0]["items"][2]["sub_items"]
  assert "_missing" not in sub_items[0]
  assert sub_items[1]["_missing"] is True


def _param_keys_from_header() -> set[str]:
  header = os.path.join(os.path.dirname(__file__), "../../../common/params_keys.h")
  with open(header) as f:
    return set(re.findall(r'\{"(\w+)",', f.read()))


def _items(node: dict):
  for item in node.get("items", []):
    yield item
    yield from item.get("sub_items", [])
  for sub in node.get("sub_panels", []):
    yield from _items(sub)


def test_real_settings_ui_keys_exist_in_params():
  """真实 settings_ui.json 的每个 key 都必须在 params_keys.h 注册。

  漏注册的后果不是显示问题：lanlink 会把它渲染成可用控件（stale libparams
  下 mark_missing_keys 也查不出），用户去调、写入时才 404。
  """
  ui_path = os.path.join(os.path.dirname(__file__), "..", "settings_ui.json")
  with open(ui_path) as f:
    real = json.load(f)
  existing = _param_keys_from_header()
  marked = mark_missing_keys(real, lambda key: key in existing)

  ghosts = []
  for panel in marked["panels"]:
    for section in panel.get("sections", []):
      ghosts += [item["key"] for item in _items(section) if item.get("_missing")]
  for brand in marked.get("vehicle_settings", {}).values():
    ghosts += [item["key"] for item in _items(brand) if item.get("_missing")]

  assert ghosts == []
