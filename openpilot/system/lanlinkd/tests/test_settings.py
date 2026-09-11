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
