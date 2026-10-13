import pytest

from openpilot.system.lanlinkd.toyota import catalog, dtc
from openpilot.system.lanlinkd.toyota.link import DiagLink

from .toyota_fakes import FakeEcu, FakePanda


class TestDecodeCode:
  @pytest.mark.parametrize("raw,code", [
    ("0133", "P0133"),
    ("4123", "C0123"),
    ("9a01", "B1A01"),
    ("c001", "U0001"),
    ("0a9b12", "P0A9B12"),   # UDS：再加 2 位故障类型
  ])
  def test_letter_and_digits(self, raw, code):
    assert dtc.decode_code(bytes.fromhex(raw)) == code


class TestParseResponse:
  def test_kwp_13_is_count_then_two_byte_codes(self):
    assert dtc.parse_response(bytes.fromhex("13"), bytes.fromhex("5302 0133 c123")) == [
      ("P0133", "确认"), ("U0123", "确认")]

  def test_13_with_suffix_uses_the_same_layout(self):
    assert dtc.parse_response(bytes.fromhex("13ff00"), bytes.fromhex("5301 0133")) == [("P0133", "确认")]

  def test_zero_count_means_no_dtcs(self):
    assert dtc.parse_response(bytes.fromhex("13"), bytes.fromhex("5300")) == []

  def test_count_limits_trailing_padding(self):
    assert dtc.parse_response(bytes.fromhex("13"), bytes.fromhex("5301 0133 0455")) == [("P0133", "确认")]

  def test_kwp_18_is_count_then_code_and_status(self):
    assert dtc.parse_response(bytes.fromhex("1800ff00"), bytes.fromhex("5802 0133e0 c12340")) == [
      ("P0133", "0xE0"), ("U0123", "0x40")]

  def test_uds_19_skips_availability_mask(self):
    assert dtc.parse_response(bytes.fromhex("1902ac"), bytes.fromhex("5902ff 0a9b1209 c0010188")) == [
      ("P0A9B12", "测试失败、确认"), ("U000101", "确认、故障指示灯亮")]

  def test_placeholder_codes_are_skipped(self):
    assert dtc.parse_response(bytes.fromhex("13"), bytes.fromhex("5303 0000 aaaa 0133")) == [("P0133", "确认")]
    assert dtc.parse_response(bytes.fromhex("1902ac"), bytes.fromhex("5902ff 00000008 aaaaaa08")) == []

  def test_negative_response_is_none(self):
    assert dtc.parse_response(bytes.fromhex("13"), bytes.fromhex("7f1311")) is None


class TestDescribe:
  def test_toyota_table_wins_over_obd2(self):
    descriptions = {"toyota": {"P0133": "丰田"}, "obd2": {"P0133": "通用", "P0171": "稀"}}
    assert dtc.describe("P0133", descriptions) == "丰田"
    assert dtc.describe("P0171", descriptions) == "稀"

  def test_uds_code_falls_back_to_five_chars(self):
    assert dtc.describe("P017112", {"toyota": {}, "obd2": {"P0171": "稀"}}) == "稀"

  def test_unknown_is_empty(self):
    assert dtc.describe("P9999", {"toyota": {}, "obd2": {}}) == ""


class TestTargets:
  def test_direct_and_gateway_targets_are_unique(self):
    targets = catalog.dtc_targets()
    keys = [t["ecu"] for t in targets]
    assert len(keys) == len(set(keys))
    assert "7E0" in keys and "750/40" in keys
    # 0x750 只用子地址访问：不带子地址的帧会被网关当成子地址解析
    assert "750" not in keys
    engine = next(t for t in targets if t["ecu"] == "7E0")
    assert engine["tx"] == 0x7E0 and engine["sub"] is None and engine["name"]


# ---- 与 ECU 交互：帧级假 ECU ----

ENGINE = (0x7E0, None)
BODY = (0x750, 0x40)
TARGETS = [{"ecu": "7E0", "tx": 0x7E0, "sub": None, "name": "发动机"}, {"ecu": "750/40", "tx": 0x750, "sub": 0x40, "name": "车身"},
           {"ecu": "7C0", "tx": 0x7C0, "sub": None, "name": "仪表"}]
DESCRIPTIONS = {"toyota": {"B1A01": "车身"}, "obd2": {"P0133": "氧传感器响应慢"}}


def make_link(ecus):
  panda = FakePanda({address: FakeEcu(responses) for address, responses in ecus.items()})
  link = DiagLink(panda, elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return panda, link


class TestScan:
  def test_reads_present_ecus_with_first_positive_command(self):
    panda, link = make_link({
      ENGINE: {b"\x3e": b"\x7e", bytes.fromhex("1800ff00"): bytes.fromhex("7f1811"),
               bytes.fromhex("1802ff00"): bytes.fromhex("5801 0133e0"),
               bytes.fromhex("18ff00"): bytes.fromhex("5801 0171e0")},
      BODY: {b"\x3e": bytes.fromhex("7f3e12"), b"\x13": bytes.fromhex("5300")},
    })
    result = dtc.scan(link, TARGETS, DESCRIPTIONS)
    assert result == [
      {"ecu": "7E0", "name": "发动机", "dtcs": [{"code": "P0133", "status": "0xE0", "description": "氧传感器响应慢"}]},
      {"ecu": "750/40", "name": "车身", "dtcs": []},
    ]
    engine_requests = panda.ecus[ENGINE].requests
    assert bytes.fromhex("18ff00") not in engine_requests   # 取到第一条正响应就停

  def test_present_ecu_that_answers_no_read_command_is_listed_empty(self):
    _, link = make_link({ENGINE: {b"\x3e": b"\x7e"}})
    assert dtc.scan(link, TARGETS[:1], DESCRIPTIONS) == [{"ecu": "7E0", "name": "发动机", "dtcs": []}]


class TestClear:
  def test_stops_at_first_positive(self):
    panda, link = make_link({BODY: {b"\x04": bytes.fromhex("7f0411"), b"\x14": b"\x54"}})
    assert dtc.clear(link, TARGETS[1]) == {"ecu": "750/40", "ok": True}
    assert panda.ecus[BODY].requests == [b"\x04", b"\x14"]

  def test_pending_then_positive_is_ok(self):
    _, link = make_link({ENGINE: {b"\x04": [bytes.fromhex("7f0478"), b"\x44"]}})
    assert dtc.clear(link, TARGETS[0]) == {"ecu": "7E0", "ok": True}

  def test_all_rejected_reports_last_error(self):
    _, link = make_link({ENGINE: {}})
    result = dtc.clear(link, TARGETS[0])
    assert result["ecu"] == "7E0" and result["ok"] is False and result["error"]
