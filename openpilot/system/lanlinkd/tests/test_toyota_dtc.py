import json

import pytest

from openpilot.system.lanlinkd.toyota import catalog, dtc
from openpilot.system.lanlinkd.toyota.link import DiagLink

from .test_toyota_customization_flow import PROBE
from .toyota_fakes import FakeEcu, FakePanda, FakeUdsEcu

UDS_CLEAR = bytes.fromhex("14ffffff")
DTC_TEXT = catalog.load_sienna()["dtcText"]


def _link(ecus: dict) -> DiagLink:
  return DiagLink(FakePanda(ecus), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)


def _address(ecu: dict) -> tuple[int, int | None]:
  return int(ecu["tx"], 16), int(ecu["sub"], 16) if ecu["sub"] else None


def _replay(ecu_id: str) -> tuple[dict, DiagLink]:
  ecu = catalog.ecu(ecu_id)
  return ecu, _link({_address(ecu): FakeUdsEcu.from_probe(PROBE, ecu["tx"], ecu["sub"])})


def _prototype_decode_dtcs(resp: str) -> list[tuple[str, int]]:
  """原型 carista/prototype/build_data.py 的 decode_dtcs（独立参照实现）。"""
  if not resp.startswith("5902"):
    return []
  b = bytes.fromhex(resp[6:])
  return [(f"{'PCBU'[b[i] >> 6]}{(b[i] >> 4) & 3}{b[i] & 15:X}{b[i+1]:02X}-{b[i+2]:02X}", b[i + 3])
          for i in range(0, len(b) - 3, 4)]


def _probe_dtc_responses() -> dict[str, str]:
  with open(PROBE) as f:
    rows = [json.loads(line) for line in f]
  return {f"{r['tx']}/{r['sub']}" if r["sub"] else r["tx"]: r["resp"] for r in rows if r.get("phase") == "dtc"}


class TestDecode:
  @pytest.mark.parametrize("raw, code", [
    ("c13187", "U0131-87"),
    ("a35987", "B2359-87"),
    ("0a9b12", "P0A9B-12"),
    ("9a01", "B1A01"),       # Toyota CAN：2 字节，没有 FTB
    ("4123", "C0123"),
  ])
  def test_code_string(self, raw, code):
    assert dtc.decode_code(bytes.fromhex(raw)) == code

  def test_text_joins_base_code_and_failure_type(self):
    assert dtc.describe("U0131-87", DTC_TEXT) == "与动力转向控制模块通信丢失（报文丢失）"
    assert dtc.describe("U0131-00", DTC_TEXT) == "与动力转向控制模块通信丢失"   # FTB 00 没有说明
    assert dtc.describe("B1A01-87", DTC_TEXT) == ""                          # 基码没有说明


class TestScanUds:
  def test_replayed_car_matches_prototype_for_every_ecu(self):
    responses = _probe_dtc_responses()
    assert len(responses) == len(catalog.load_sienna()["ecus"]) == 41
    for ecu_id, resp in responses.items():
      ecu, link = _replay(ecu_id)
      result = dtc.scan_ecu(link, ecu, DTC_TEXT)
      if ecu["protocol"] == "uds":
        assert result["status"] == "ok", ecu_id
        assert [(d["code"], d["status"]) for d in result["dtcs"]] == _prototype_decode_dtcs(resp), ecu_id

  def test_body_ecu_codes_with_text(self):
    ecu, link = _replay("750/40")
    result = dtc.scan_ecu(link, ecu, DTC_TEXT)
    assert (result["ecu"], result["protocol"]) == ("750/40", "uds")
    assert result["dtcs"][0] == {"code": "B2359-87", "status": 0x29, "text": "右侧智能 LDM（报文丢失）"}
    assert [d["code"] for d in result["dtcs"]] == ["B2359-87", "U0163-87", "U023A-87"]

  def test_all_zero_code_is_skipped(self):
    ecu = catalog.ecu("7B0")
    fake = FakeEcu({bytes.fromhex("19028d"): bytes.fromhex("5902ff 00000000 c1318728")})
    result = dtc.scan_ecu(_link({(0x7B0, None): fake}), ecu, DTC_TEXT)
    assert [d["code"] for d in result["dtcs"]] == ["U0131-87"]

  def test_offline_ecu(self):
    result = dtc.scan_ecu(_link({}), catalog.ecu("750/40"), DTC_TEXT)
    assert (result["status"], result["dtcs"]) == ("offline", [])

  def test_rejected_read_is_reported_not_raised(self):
    fake = FakeEcu({bytes.fromhex("19028d"): bytes.fromhex("7f1922")})
    result = dtc.scan_ecu(_link({(0x7B0, None): fake}), catalog.ecu("7B0"), DTC_TEXT)
    assert (result["status"], result["dtcs"], result["readError"]) == ("ok", [], "NRC 22")

  def test_malformed_response(self):
    fake = FakeEcu({bytes.fromhex("19028d"): bytes.fromhex("5902ff") + bytes(range(24))}, misnumbered_frames=True)
    result = dtc.scan_ecu(_link({(0x7B0, None): fake}), catalog.ecu("7B0"), DTC_TEXT)
    assert result["status"] == "malformed" and "invalid consecutive frame index" in result["malformedReason"]


class TestScanToyotaCan:
  def test_tries_pids_in_order_and_codes_have_no_status(self):
    window = FakeEcu({bytes.fromhex("1381"): bytes.fromhex("7f1312"), bytes.fromhex("1380"): bytes.fromhex("5302 9a01 c131")})
    result = dtc.scan_ecu(_link({(0x750, 0x90): window}), catalog.ecu("750/90"), DTC_TEXT)
    assert window.requests == [bytes.fromhex("1381"), bytes.fromhex("1380")]
    assert (result["status"], result["protocol"]) == ("ok", "toyota_can")
    # B1A01 两张码表都没有 → 空说明；Toyota CAN 的码没有 FTB
    assert result["dtcs"] == [{"code": "B1A01", "status": None, "text": ""},
                              {"code": "U0131", "status": None, "text": "与动力转向控制模块通信丢失"}]

  def test_silent_pid_does_not_stop_the_next_one(self):
    window = FakeEcu({bytes.fromhex("1380"): bytes.fromhex("5301 9a01")})   # 13 81 无响应
    result = dtc.scan_ecu(_link({(0x750, 0x90): window}), catalog.ecu("750/90"), DTC_TEXT)
    assert (result["status"], [d["code"] for d in result["dtcs"]]) == ("ok", ["B1A01"])

  def test_offline_only_when_every_pid_is_silent(self):
    window = FakeEcu()
    result = dtc.scan_ecu(_link({(0x750, 0x90): window}), catalog.ecu("750/90"), DTC_TEXT)
    assert result["status"] == "offline" and len(window.requests) == 3

  def test_count_limits_trailing_padding(self):
    window = FakeEcu({bytes.fromhex("1381"): bytes.fromhex("5301 9a01 0455")})
    result = dtc.scan_ecu(_link({(0x750, 0x90): window}), catalog.ecu("750/90"), DTC_TEXT)
    assert [d["code"] for d in result["dtcs"]] == ["B1A01"]

  def test_all_pids_rejected(self):
    window = FakeEcu({bytes([0x13, pid]): bytes.fromhex("7f1312") for pid in (0x81, 0x80, 0xB0)})
    result = dtc.scan_ecu(_link({(0x750, 0x90): window}), catalog.ecu("750/90"), DTC_TEXT)
    assert len(window.requests) == 3
    assert (result["status"], result["dtcs"], result["readError"]) == ("ok", [], "NRC 12")


class TestClear:
  @pytest.mark.parametrize("ecu_id, address, request_hexes", [
    ("750/40", (0x750, 0x40), ["1003", "14ffffff"]),
    ("750/90", (0x750, 0x90), ["14"]),
  ])
  def test_clear_by_protocol(self, ecu_id, address, request_hexes):
    fake = FakeEcu({b"\x10\x03": b"\x50\x03", bytes.fromhex(request_hexes[-1]): [b"\x7f\x14\x78", b"\x54"]})
    assert dtc.clear(_link({address: fake}), catalog.ecu(ecu_id)) == {"ecu": ecu_id, "ok": True}
    assert fake.requests == [bytes.fromhex(h) for h in request_hexes]

  def test_uds_ecu_that_refuses_clear_in_default_session(self):
    fake = FakeUdsEcu({}, {UDS_CLEAR: [b"\x7f\x14\x78", b"\x54"]}, clear_needs_extended_session=True)
    assert dtc.clear(_link({(0x750, 0x29): fake}), catalog.ecu("750/29")) == {"ecu": "750/29", "ok": True}
    assert fake.requests == [bytes.fromhex("1003"), UDS_CLEAR]

  def test_obd_clear_first(self):
    fake = FakeEcu({b"\x04": b"\x44"})
    assert dtc.clear(_link({(0x7D2, None): fake}), catalog.ecu("7D2")) == {"ecu": "7D2", "ok": True}
    assert fake.requests == [b"\x04"]

  def test_obd_first_falls_back_to_uds_clear(self):
    fake = FakeUdsEcu({}, {UDS_CLEAR: b"\x54"})
    assert dtc.clear(_link({(0x7B0, None): fake}), catalog.ecu("7B0")) == {"ecu": "7B0", "ok": True}
    assert fake.requests == [b"\x04", bytes.fromhex("1003"), UDS_CLEAR]

  def test_uds_first_falls_back_to_obd_clear(self):
    fake = FakeUdsEcu({}, {UDS_CLEAR: bytes.fromhex("7f1411"), b"\x04": b"\x44"})
    assert dtc.clear(_link({(0x705, None): fake}), catalog.ecu("705")) == {"ecu": "705", "ok": True}
    assert fake.requests == [bytes.fromhex("1003"), UDS_CLEAR, b"\x04"]

  def test_rejected(self):
    fake = FakeUdsEcu({}, {UDS_CLEAR: bytes.fromhex("7f1422")})
    assert dtc.clear(_link({(0x750, 0x40): fake}), catalog.ecu("750/40")) == {"ecu": "750/40", "ok": False, "error": "NRC 22"}

  def test_offline(self):
    assert dtc.clear(_link({}), catalog.ecu("750/40")) == {"ecu": "750/40", "ok": False, "error": "无响应"}
