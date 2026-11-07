import pytest

from openpilot.system.lanlinkd.toyota import coding

# 实车 750/40 车身 ECU 对 22 2200 的应答（去掉 62 22 00）：DID 22xx 的支持位图
BODY_BITMAP_22XX = bytes.fromhex("8001007180b9e001a07b00400001880600002000000008700000000000000000")


class TestUdsDidSupported:
  @pytest.mark.parametrize("did, supported", [
    (0x2201, True),    # 首字节 bit7
    (0x2202, False),   # 实车读 22 2202 回 7F 22 31
    (0x2270, True),    # 开门时大灯未关提醒，实车读到 62 2270 01
  ])
  def test_matches_what_the_car_answered(self, did, supported):
    assert coding.uds_did_supported(BODY_BITMAP_22XX, did) is supported

  def test_bitmap_must_be_32_bytes(self):
    assert coding.uds_did_supported(BODY_BITMAP_22XX[:31], 0x2201) is False


class TestValueBits:
  def test_extract_shifts_masked_bits_down(self):
    assert coding.extract_value(bytes.fromhex("00b4"), 1, 0x30) == 3

  def test_insert_replaces_only_masked_bits(self):
    assert coding.insert_value(bytes.fromhex("00b4"), 1, 0x30, 1) == bytes.fromhex("0094")

  def test_insert_rejects_value_wider_than_mask(self):
    with pytest.raises(ValueError):
      coding.insert_value(bytes.fromhex("00b4"), 1, 0x30, 4)

  def test_short_payload_raises(self):
    with pytest.raises(ValueError):
      coding.extract_value(b"\x01", 1, 0xFF)


class TestToyotaCanMasks:
  def test_parse_and_expand(self):
    # A8 03 应答：E8 03 + [id, len, mask…]；21 读到的是只含掩码非零字节的压缩数据
    masks = coding.parse_masks(bytes.fromhex("e803 11 03 ff0030 12 01 02"))
    assert masks == {0x11: bytes.fromhex("ff0030"), 0x12: b"\x02"}
    assert coding.expand_by_mask(bytes.fromhex("a020"), masks[0x11]) == bytes.fromhex("a00020")

  @pytest.mark.parametrize("byte_index, mask, supported", [(2, 0x20, True), (2, 0x40, False), (1, 0x01, False), (3, 0x01, False)])
  def test_setting_supported_when_mask_covers_its_bits(self, byte_index, mask, supported):
    assert coding.can_setting_supported(bytes.fromhex("ff0030"), byte_index, mask) is supported
