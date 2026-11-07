import json
import os

import pytest

from openpilot.system.lanlinkd.toyota import catalog, live_data
from openpilot.system.lanlinkd.toyota.link import DiagLink

from .test_toyota_customization_flow import PROBE
from .toyota_fakes import FakeEcu, FakePanda, FakeUdsEcu

# 快照 carista/sienna_snapshot.json 的 probe_values.ready（与夹具同一次 Ready 探测）
READY_VALUES = {
  "car_tool_front_right_wheel_speed": 0.0, "car_tool_front_left_wheel_speed": 0.0, "car_tool_rear_right_wheel_speed": 0.0,
  "car_tool_rear_left_wheel_speed": 0.0, "car_tool_compressor_speed": 0.0, "car_tool_inside_temp": 22.85,
  "car_tool_battery_voltage_engine": 14.248, "car_tool_engine_speed": 0.0, "car_calculated_load": 0.0,
  "car_tool_coolant_temp": 24.0,
  **{f"car_ev_or_hybrid_battery_n1_block_{n}_voltage": v for n, v in enumerate(
    [15.249, 15.199, 30.3394, 30.2698, 30.2588, 30.21, 30.2393, 30.2295, 30.2698, 30.3198, 15.199, 15.2197], start=1)},
  **{f"car_ev_or_hybrid_battery_n1_block_{n}_internal_resistance": r for n, r in enumerate(
    [23.0, 22.0, 22.0, 22.0, 22.0, 22.0, 22.0, 22.0, 22.0, 22.0, 23.0, 23.0], start=1)},
  "car_tool_mileage": 13875, "car_tool_mileage_unit": 1,
}


# 数据文件必须存在（不 skip）：否则排除集合为空，守护会悄悄失效
with open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "tools", "lanlink", "sienna_techstream_items.json")) as _f:
  _TECHSTREAM_KEYS = {i["key"] for i in json.load(_f)["live"]}


def _replay_car() -> DiagLink:
  ecus = {}
  for item in catalog.load_sienna()["live"]:
    ecu = catalog.ecu(item["ecu"])
    address = (int(ecu["tx"], 16), int(ecu["sub"], 16) if ecu["sub"] else None)
    ecus.setdefault(address, FakeUdsEcu.from_probe(PROBE, ecu["tx"], ecu["sub"]))
  return DiagLink(FakePanda(ecus), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)


def _items(*keys: str) -> list[dict]:
  return [catalog.live_item(k) for k in keys]


class TestReplayedCar:
  def test_every_item_matches_the_snapshot(self):
    # 夹具只覆盖 Carista 的 36 项；Techstream 新增项未上车验证，没有回放数据，显式排除
    items = [i for i in catalog.load_sienna()["live"] if i["key"] not in _TECHSTREAM_KEYS]
    assert {i["key"] for i in items} == set(READY_VALUES)
    values = {v["key"]: v for v in live_data.read(_replay_car(), items)}
    assert {k: v["value"] for k, v in values.items()} == pytest.approx(READY_VALUES, abs=1e-3)
    assert all("error" not in v for v in values.values())

  def test_text_follows_precision_and_options(self):
    values = {v["key"]: v["text"] for v in live_data.read(_replay_car(), _items(
      "car_tool_inside_temp", "car_tool_battery_voltage_engine", "car_tool_mileage", "car_tool_mileage_unit"))}
    assert values == {"car_tool_inside_temp": "22.85", "car_tool_battery_voltage_engine": "14.2",
                      "car_tool_mileage": "13875", "car_tool_mileage_unit": "公里"}

  def test_items_sharing_a_did_cost_one_request(self):
    link = _replay_car()
    battery = [i for i in catalog.load_sienna()["live"] if i["ecu"] == "7D2" and i["key"] in READY_VALUES]
    live_data.read(link, battery)
    requests = [r for ecu in link.panda.ecus.values() for r in ecu.requests]
    assert sorted(requests) == [bytes.fromhex("221809"), bytes.fromhex("221813")]


class TestDecode:
  def test_signed_uses_mask_width(self):
    fake = FakeUdsEcu({0x1001: bytes.fromhex("ff38")}, {})   # -200 × 0.01
    link = DiagLink(FakePanda({(0x7C4, None): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    assert live_data.read(link, _items("car_tool_inside_temp")) == [{"key": "car_tool_inside_temp", "value": -2.0, "text": "-2.00"}]

  def test_unknown_option_value_falls_back_to_number(self):
    fake = FakeUdsEcu({0x0103: bytes.fromhex("07003633")}, {})
    link = DiagLink(FakePanda({(0x700, None): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    assert live_data.read(link, _items("car_tool_mileage_unit"))[0]["text"] == "7"


class TestErrors:
  def test_each_failed_item_carries_its_own_error(self):
    engine = FakeEcu({bytes.fromhex("221f42"): bytes.fromhex("7f2231"), bytes.fromhex("221f05"): bytes.fromhex("621f05")})
    link = DiagLink(FakePanda({(0x700, None): engine}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    values = live_data.read(link, _items("car_tool_battery_voltage_engine", "car_tool_coolant_temp", "car_tool_inside_temp"))
    assert values == [
      {"key": "car_tool_battery_voltage_engine", "value": None, "text": "", "error": "NRC 31"},
      {"key": "car_tool_coolant_temp", "value": None, "text": "", "error": "应答太短"},
      {"key": "car_tool_inside_temp", "value": None, "text": "", "error": "无响应"},
    ]

  def test_malformed_response(self):
    garbled = FakeEcu({bytes.fromhex("221809"): b"\x62\x18\x09" + bytes(24)}, misnumbered_frames=True)
    link = DiagLink(FakePanda({(0x7D2, None): garbled}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    assert live_data.read(link, _items("car_ev_or_hybrid_battery_n1_block_1_voltage"))[0]["error"] == "应答异常"
