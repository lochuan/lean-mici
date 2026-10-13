import pytest

from openpilot.system.lanlinkd.toyota import customization as cz

# 一个 0x750/40 车身 ECU：本地 ID 12 的掩码是 [FF, 00, 30]（第 2 字节不可写），
# 读回的压缩数据只含掩码非零的 2 个字节
A803_RESPONSE = bytes.fromhex("e803") + bytes.fromhex("12 03 ff0030") + bytes.fromhex("14 01 80")

ITEM = {
  "uid": 1, "group": "Doors", "name": {"en": "Beep", "zh": "提示音"}, "description": {"en": "", "zh": ""},
  "tx": "750", "sub": "40", "readId": "12", "writeId": "12", "readOnly": False,
  "options": [
    {"title": {"en": "On", "zh": "开"}, "bits": [[1, 5, 1], [1, 4, 0]]},
    {"title": {"en": "Off", "zh": "关"}, "bits": [[1, 5, 0], [1, 4, 0]]},
  ],
}


class TestParseMasks:
  def test_parses_id_length_mask_records(self):
    assert cz.parse_masks(A803_RESPONSE) == {0x12: bytes.fromhex("ff0030"), 0x14: bytes.fromhex("80")}

  def test_negative_or_garbage_response_is_empty(self):
    assert cz.parse_masks(bytes.fromhex("7fa811")) == {}
    assert cz.parse_masks(bytes.fromhex("e803 12 05 ff")) == {}  # 截断记录直接丢弃


class TestSupported:
  def test_supported_when_every_option_bit_is_in_mask(self):
    assert cz.is_supported(ITEM, cz.parse_masks(A803_RESPONSE))

  def test_unsupported_when_id_missing(self):
    assert not cz.is_supported(ITEM, {0x14: b"\x80"})

  def test_unsupported_when_bit_outside_mask(self):
    item = {**ITEM, "options": [{"title": ITEM["options"][0]["title"], "bits": [[1, 0, 1]]}]}
    assert not cz.is_supported(item, cz.parse_masks(A803_RESPONSE))


class TestCurrentOption:
  def test_matches_first_option_whose_bits_all_match(self):
    assert cz.current_option(ITEM, bytes.fromhex("a020")) == 0
    assert cz.current_option(ITEM, bytes.fromhex("a000")) == 1

  def test_no_match_is_none(self):
    assert cz.current_option(ITEM, bytes.fromhex("a030")) is None

  def test_short_data_is_none(self):
    assert cz.current_option(ITEM, bytes.fromhex("a0")) is None


class TestBuildWrite:
  def test_changes_only_option_bits(self):
    assert cz.apply_option(ITEM, bytes.fromhex("a030"), 1) == bytes.fromhex("a000")

  def test_expand_fills_masked_out_bytes_with_zero(self):
    # 压缩数据 [a0, 20] 按掩码 [ff, 00, 30] 展开成 [a0, 00, 20]
    assert cz.expand(bytes.fromhex("a020"), bytes.fromhex("ff0030")) == bytes.fromhex("a00020")

  def test_write_request(self):
    assert cz.write_request(ITEM, bytes.fromhex("a020"), bytes.fromhex("ff0030")) == bytes.fromhex("3b12a00020")

  def test_read_only_item_refuses(self):
    with pytest.raises(ValueError):
      cz.apply_option({**ITEM, "readOnly": True}, bytes.fromhex("a020"), 0)
