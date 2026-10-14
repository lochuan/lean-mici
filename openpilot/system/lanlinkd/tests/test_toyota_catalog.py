from openpilot.system.lanlinkd.toyota import catalog


# ---- Sienna 4 代目录（Carista 数据源，tools/lanlink/build_toyota_sienna_catalog.py 生成）----

def _is_chinese(text: str) -> bool:
  return any("一" <= ch <= "鿿" for ch in text)


class TestSiennaCatalog:
  def test_counts_match_the_car(self):
    sienna = catalog.load_sienna()
    # 实车报告支持 132 项定制，剔除两项脚踢感应灵敏度；41 个 ECU 应答；36 项实时数据（24 项是动力电池）
    assert len(sienna["settings"]) == 130
    assert len(sienna["ecus"]) == 41
    assert len(sienna["live"]) == 36
    assert len(sienna["categories"]) == 18
    assert [t["id"] for t in sienna["liveTools"]] == ["wheels", "engine", "hvac"]
    assert not [s for s in sienna["settings"] if "kick_sensor" in s["key"]]

  def test_window_ecus_use_toyota_can(self):
    sienna = catalog.load_sienna()
    assert {e["id"] for e in sienna["ecus"] if e["protocol"] == "toyota_can"} == {"750/90", "750/91", "750/92", "750/93", "750/EC", "750/AD"}
    assert all((s["localId"] is None) == (catalog.ecu(s["ecu"])["protocol"] == "uds") for s in sienna["settings"])

  def test_everything_user_facing_is_chinese(self):
    sienna = catalog.load_sienna()
    names = ([e["name"] for e in sienna["ecus"]] + [c["name"] for c in sienna["categories"]]
             + [s["name"] for s in sienna["settings"]] + [i["name"] for i in sienna["live"]] + [t["name"] for t in sienna["liveTools"]])
    assert [n for n in names if not _is_chinese(n)] == []
    # 选项可以是数值（空调温度补偿 ±3 °C），但不能留英文
    labels = [o["label"] for s in sienna["settings"] for o in s["options"]]
    assert [label for label in labels if not _is_chinese(label) and not any(ch.isdigit() for ch in label)] == []

  def test_references_resolve(self):
    sienna = catalog.load_sienna()
    categories = {c["id"] for c in sienna["categories"]}
    live_keys = {i["key"] for i in sienna["live"]}
    assert all(s["category"] in categories and catalog.ecu(s["ecu"]) for s in sienna["settings"])
    assert all(catalog.ecu(i["ecu"]) for i in sienna["live"])
    assert all(k in live_keys for t in sienna["liveTools"] for k in t["keys"])
    assert sum(s["popular"] for s in sienna["settings"]) == 8

  def test_lookups(self):
    headlights = catalog.setting("car_setting_warn_headlights_on_door_open")
    assert (headlights["ecu"], headlights["did"], headlights["byteIndex"], headlights["mask"]) == ("750/40", "2270", 0, "FF")
    assert headlights["options"] == [{"value": 1, "label": "开启"}, {"value": 0, "label": "关闭"}]
    assert catalog.ecu("750/40")["tx"] == "750" and catalog.ecu("750/40")["sub"] == "40"
    assert catalog.ecu("7B0")["sub"] is None
    assert catalog.setting("nope") is None and catalog.ecu("123") is None
    wheel = catalog.live_item("car_tool_front_right_wheel_speed")
    assert (wheel["ecu"], wheel["did"], wheel["mult"], wheel["unit"]) == ("7B0", "1002", 0.1, "km/h")

  def test_battery_layout(self):
    blocks = catalog.load_sienna()["battery"]["blocks"]
    # 块 1、2、11、12 是半块（2 模组），其余整块（4 模组），共 40 模组
    assert [b["modules"] for b in blocks] == [2, 2, 4, 4, 4, 4, 4, 4, 4, 4, 2, 2]
    assert blocks[0]["voltageKey"] == "car_ev_or_hybrid_battery_n1_block_1_voltage"
    assert catalog.live_item(blocks[11]["resistanceKey"])["did"] == "1813"

  def test_dtc_text_splits_base_code_and_failure_type(self):
    dtc_text = catalog.load_sienna()["dtcText"]
    assert dtc_text["codes"]["U0131"] == {"text": "与动力转向控制模块通信丢失", "guess": False}
    assert dtc_text["codes"]["B2359"]["guess"] is True
    assert dtc_text["ftb"] == {"87": "报文丢失", "13": "线路开路"}

  def test_engine_dependent_live_items_are_marked(self):
    marked = {i["key"] for i in catalog.load_sienna()["live"] if i["needsEngineRunning"]}
    assert marked == {"car_tool_engine_speed", "car_calculated_load", "car_tool_coolant_temp"}
