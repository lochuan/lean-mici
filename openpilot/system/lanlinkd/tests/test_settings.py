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
