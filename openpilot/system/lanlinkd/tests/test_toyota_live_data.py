import pytest

from openpilot.system.lanlinkd.toyota import live_data as ld
from openpilot.system.lanlinkd.toyota.link import DiagLink

from .toyota_fakes import FakeEcu, FakePanda


def pid(id_, **fields):
  base = {"id": id_, "name": {"en": f"pid{id_}"}, "hdr": "7E0", "sub": None, "cmd": "2104", "type": 1, "sbi": 0, "dl": 1,
          "bit": 0, "mul": 1.0, "div": 1.0, "ofs": 0.0, "signed": False, "formula": "", "unit": ""}
  return {**base, **fields}


class TestDefaultProfile:
  @pytest.mark.parametrize("vin,profile", [
    ("JTMB1RFV5ND123456", ld.PROFILE_2016_ON),     # N = 2022
    ("JTDKN3DU0G0123456", ld.PROFILE_2016_ON),     # G = 2016
    ("JTDKN3DU0F0123456", ld.PROFILE_2009_2015),   # F = 2015
    ("JTDKN3DU090123456", ld.PROFILE_2009_2015),   # 9 = 2009
    ("JTDKN3DU070123456", ld.PROFILE_UP_TO_2008),  # 7 = 2007
    ("", ld.PROFILE_2016_ON),
    ("bogus", ld.PROFILE_2016_ON),
  ])
  def test_by_model_year(self, vin, profile):
    assert ld.default_profile_id(vin) == profile


class TestReadablePids:
  def test_keeps_only_11bit_can_reads(self):
    pids = [pid(1), pid(2, type=3, cmd="3029060101"), pid(3, hdr="DA62E0", cmd="2290"), pid(4, hdr="812AF0"),
            pid(5, hdr="", cmd="01B4"), pid(6, hdr="750", sub="2A", cmd="221001"), pid(9, type=0, formula="")]
    assert [p["id"] for p in ld.readable_pids(pids)] == [1, 6]

  def test_computed_pid_needs_all_references_readable(self):
    pids = [pid(1), pid(3, hdr="DA62E0"), pid(7, hdr="", cmd="", type=0, formula="PID(1)*2"),
            pid(8, hdr="", cmd="", type=0, formula="PID(1)-PID(3)")]
    assert [p["id"] for p in ld.readable_pids(pids)] == [1, 7]


class TestResponseData:
  def test_strips_sid_and_echo(self):
    assert ld.response_data("2104", bytes.fromhex("6104 aabb")) == bytes.fromhex("aabb")
    assert ld.response_data("220103", bytes.fromhex("620103 0102")) == bytes.fromhex("0102")
    assert ld.response_data("01B4", bytes.fromhex("41b4 80")) == bytes.fromhex("80")

  def test_negative_or_mismatched_is_none(self):
    assert ld.response_data("2104", bytes.fromhex("7f2112")) is None
    assert ld.response_data("220103", bytes.fromhex("620104 01")) is None


class TestDecode:
  def test_byte_set_big_endian_scaled(self):
    assert ld.decode(pid(1, sbi=1, dl=2, mul=2.0, div=10.0, ofs=-40.0), bytes.fromhex("ff0102"), {}) == pytest.approx(
      0x0102 * 2 / 10 - 40)

  def test_byte_set_signed_and_div_zero_means_one(self):
    assert ld.decode(pid(1, dl=2, signed=True, div=0.0), bytes.fromhex("fffe"), {}) == -2

  def test_byte_set_dl_zero_reads_to_end_up_to_four_bytes(self):
    assert ld.decode(pid(1, sbi=1, dl=0), bytes.fromhex("00 01 02 03 04 05"), {}) == 0x01020304

  def test_bit_value_ignores_scaling(self):
    item = pid(1, type=2, sbi=1, bit=7, mul=5.0, ofs=3.0)
    assert ld.decode(item, bytes.fromhex("00 80"), {}) == 1
    assert ld.decode(item, bytes.fromhex("00 7f"), {}) == 0

  def test_formula_letters_index_data_from_zero(self):
    assert ld.decode(pid(1, type=0, sbi=1, formula="(A*256+B)/1000", mul=9.0), bytes.fromhex("1234"), {}) == pytest.approx(
      0x1234 / 1000)

  def test_formula_two_letter_bytes(self):
    data = bytes(range(30))
    assert ld.decode(pid(1, type=0, formula="AA*256+AB"), data, {}) == 26 * 256 + 27

  def test_short_response_raises(self):
    with pytest.raises(ld.DecodeError):
      ld.decode(pid(1, sbi=2, dl=2), bytes.fromhex("0102"), {})
    with pytest.raises(ld.DecodeError):
      ld.decode(pid(1, type=0, formula="C"), bytes.fromhex("0102"), {})


class TestFormatValue:
  @pytest.mark.parametrize("value,text", [(1.0, "1"), (12.3456, "12.35"), (-0.5, "-0.5"), (123456.0, "123456")])
  def test_trims_trailing_zeros(self, value, text):
    assert ld.format_value(value) == text


# ---- 与 ECU 交互 ----

def make_link(ecus):
  panda = FakePanda({address: FakeEcu(responses) for address, responses in ecus.items()})
  return panda, DiagLink(panda, elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)


class TestRead:
  PIDS = {p["id"]: p for p in [
    pid(1, sbi=0), pid(2, sbi=1, mul=2.0),
    pid(3, hdr="750", sub="2A", cmd="221001", type=2, sbi=0, bit=0),
    pid(4, hdr="7C0", cmd="2101"),
    pid(5, hdr="", cmd="", type=0, formula="PID(1)+PID(2)"),
  ]}

  def test_groups_same_request_and_reports_errors_per_pid(self):
    panda, link = make_link({(0x7E0, None): {bytes.fromhex("2104"): bytes.fromhex("6104 0a 14")},
                             (0x750, 0x2A): {bytes.fromhex("221001"): bytes.fromhex("621001 01")}})
    values = {v["id"]: v for v in ld.read(link, self.PIDS, [2, 1, 3, 4, 99])}
    assert list(values) == [2, 1, 3, 4, 99]
    assert values[1] == {"id": 1, "value": 10, "text": "10"}
    assert values[2]["value"] == 40
    assert values[3]["value"] == 1
    assert values[4]["value"] is None and "无响应" in values[4]["error"]
    assert values[99]["value"] is None and values[99]["error"]
    assert panda.ecus[(0x7E0, None)].requests == [bytes.fromhex("2104")]   # 同一请求只发一次

  def test_computed_pid_reads_its_references(self):
    _, link = make_link({(0x7E0, None): {bytes.fromhex("2104"): bytes.fromhex("6104 0a 14")}})
    assert ld.read(link, self.PIDS, [5]) == [{"id": 5, "value": 50, "text": "50"}]

  def test_negative_response_is_an_error(self):
    _, link = make_link({(0x7E0, None): {bytes.fromhex("2104"): bytes.fromhex("7f2131")}})
    value = ld.read(link, self.PIDS, [1])[0]
    assert value["value"] is None and "7f2131" in value["error"]
