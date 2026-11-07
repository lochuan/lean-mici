"""定制项的位运算（纯函数，规则来自 Carista 逆向，见 libCarista.so 的 ByteUtils / ToyotaUdsSupportedDids）。

- UDS：22 XX00 应答是 DID XX01～XXFF 的支持位图（恰好 32 字节），DID 低字节 L 的位 =
  bitmap[(L-1)>>3] >> ((-L)&7) & 1，即首字节 bit7 对应 XX01。实车 189 个 DID 实读全部吻合。
- Toyota CAN：A8 03 → E8 03 后跟若干 [id, len, mask[len]]；21 id 读到的只含掩码非零的字节（压缩），
  按掩码展开（掩码 0 的位置补 0）后，Carista 的 byteIndex 才是下标。
- 取值/写值：payload[byteIndex] 里 mask 覆盖的位，移到最低位（目录里的掩码都是 1 字节）。
"""
UDS_BITMAP_LEN = 32
POSITIVE_A8 = 0xE8


def uds_did_supported(bitmap: bytes, did: int) -> bool:
  low = did & 0xFF
  if len(bitmap) != UDS_BITMAP_LEN or low == 0:
    return False
  return bool(bitmap[(low - 1) >> 3] >> ((-low) & 7) & 1)


def _shift(mask: int) -> int:
  return (mask & -mask).bit_length() - 1


def _check_index(payload: bytes, byte_index: int) -> None:
  if byte_index >= len(payload):
    raise ValueError(f"数据只有 {len(payload)} 字节，没有第 {byte_index} 字节")


def extract_value(payload: bytes, byte_index: int, mask: int) -> int:
  _check_index(payload, byte_index)
  return (payload[byte_index] & mask) >> _shift(mask)


def insert_value(payload: bytes, byte_index: int, mask: int, value: int) -> bytes:
  _check_index(payload, byte_index)
  shifted = value << _shift(mask)
  if value < 0 or shifted & ~mask:
    raise ValueError(f"值 {value} 超出掩码 {mask:02X} 的范围")
  out = bytearray(payload)
  out[byte_index] = (out[byte_index] & ~mask) | shifted
  return bytes(out)


def parse_masks(response: bytes) -> dict[int, bytes]:
  masks: dict[int, bytes] = {}
  if len(response) < 2 or response[0] != POSITIVE_A8:
    return masks
  body = response[2:]
  while len(body) >= 2:
    local_id, length = body[0], body[1]
    if len(body) < 2 + length:
      break
    masks[local_id] = bytes(body[2:2 + length])
    body = body[2 + length:]
  return masks


def expand_by_mask(compressed: bytes, mask: bytes) -> bytes:
  values = iter(compressed)
  return bytes(next(values, 0) if m else 0 for m in mask)


def can_setting_supported(mask_entry: bytes, byte_index: int, mask: int) -> bool:
  return byte_index < len(mask_entry) and mask_entry[byte_index] & mask == mask
