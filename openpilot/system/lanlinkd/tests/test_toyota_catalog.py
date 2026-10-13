from openpilot.system.lanlinkd.toyota import catalog


def test_shipped_data_is_consistent():
  items = catalog.customization_items()
  assert len(items) == 114
  assert {i["uid"] for i in items if i["readOnly"]} == {10038}
  for item in items:
    assert catalog.group_title(item["group"])
    assert all(len(bit) == 3 for o in item["options"] for bit in o["bits"])


def test_ecu_names():
  assert catalog.ecu_name("750", "40").startswith("750/40 ")
  assert catalog.ecu_name("7E0", None).startswith("7E0 ")
  assert catalog.ecu_name("123", None) == "123"


def test_every_ecu_has_chinese_name():
  ecus = catalog.load("ecus")
  untranslated = [e["name"]["en"] for e in ecus["direct"] + ecus["gateway"]
                  if not any("一" <= ch <= "鿿" for ch in e["name"]["zh"])]
  assert untranslated == []
