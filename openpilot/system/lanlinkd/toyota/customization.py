"""Toyota 定制项的纯逻辑（复刻 Car Scanner CustomizableCodingTemplate + ToyotaA8Item）。

- A8 03 → E8 03 后跟若干条 [id, len, mask[len]]：每个本地 ID 哪些位可写。
- 21 id → 61 id <data>：data 只含掩码非零的字节（压缩形式），BIT:n:b 的 n 是它的下标。
- 写：改位 → 按掩码展开（掩码 0 的位置填 0）→ 3B id <展开数据>，期望 7B id。
"""
from openpilot.system.lanlinkd.toyota.link import NoResponseError

POSITIVE_A8 = 0xE8


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


def _writable_bytes(mask: bytes) -> bytes:
  return bytes(b for b in mask if b)


def is_supported(item: dict, masks: dict[int, bytes]) -> bool:
  mask = masks.get(int(item["writeId"], 16))
  if not mask:
    return False
  writable = _writable_bytes(mask)
  return all(n < len(writable) and writable[n] & (1 << b)
             for option in item["options"] for n, b, _ in option["bits"])


def current_option(item: dict, data: bytes) -> int | None:
  for index, option in enumerate(item["options"]):
    if all(n < len(data) and bool(data[n] & (1 << b)) == bool(v) for n, b, v in option["bits"]):
      return index
  return None


def apply_option(item: dict, data: bytes, option_index: int) -> bytes:
  if item["readOnly"]:
    raise ValueError("该项读写 ID 不一致，禁止写入")
  out = bytearray(data)
  for n, b, v in item["options"][option_index]["bits"]:
    out[n] = out[n] | (1 << b) if v else out[n] & ~(1 << b)
  return bytes(out)


def expand(data: bytes, mask: bytes) -> bytes:
  values = iter(data)
  return bytes(next(values, m) if m else 0 for m in mask)


def read_request(item: dict) -> bytes:
  return bytes([0x21, int(item["readId"], 16)])


def write_request(item: dict, data: bytes, mask: bytes) -> bytes:
  return bytes([0x3B, int(item["writeId"], 16)]) + expand(data, mask)


# ---- 与 ECU 交互的流程（link 只需提供 request(tx, sub, payload)）----

class EcuRejectedError(Exception):
  def __init__(self, request: bytes, response: bytes):
    self.nrc = response[2] if len(response) >= 3 and response[0] == 0x7F else None
    super().__init__(f"ECU 拒绝 {request[:2].hex()}：{response.hex()}")


class StaleDataError(Exception):
  """预览之后 ECU 里的数据变了，拒绝按旧预览写入。"""


class BackupError(Exception):
  """写前备份没存下来（如 /data 不可写）：不写入。"""


def _address(item: dict) -> tuple[int, int | None]:
  return int(item["tx"], 16), int(item["sub"], 16) if item["sub"] else None


def _expect(link, item: dict, request: bytes, positive: int) -> bytes:
  response = link.request(*_address(item), request)
  if not response or response[0] != positive:
    raise EcuRejectedError(request, response)
  return response


def read_data(link, item: dict) -> bytes:
  return _expect(link, item, read_request(item), 0x61)[2:]


def read_mask(link, item: dict) -> bytes:
  mask = parse_masks(link.request(*_address(item), b"\xa8\x03")).get(int(item["writeId"], 16))
  if not mask:
    raise EcuRejectedError(b"\xa8\x03", b"")
  return mask


def scan(link, items: list[dict]) -> list[dict]:
  """探测 ECU → A803 掩码 → 读出支持项的当前值。不在线的 ECU 整体跳过。"""
  masks_by_ecu: dict[tuple, dict[int, bytes] | None] = {}
  data_cache: dict[tuple, bytes | None] = {}
  found = []
  for item in items:
    address = _address(item)
    if address not in masks_by_ecu:
      try:
        link.request(*address, b"\x3e")
        masks_by_ecu[address] = parse_masks(link.request(*address, b"\xa8\x03"))
      except NoResponseError:
        masks_by_ecu[address] = None
    masks = masks_by_ecu[address]
    if not masks or not is_supported(item, masks):
      continue
    key = (address, item["readId"])
    if key not in data_cache:
      try:
        data_cache[key] = read_data(link, item)
      except (NoResponseError, EcuRejectedError):
        data_cache[key] = None
    data = data_cache[key]
    if data is None:
      continue
    found.append({**item, "current": current_option(item, data), "raw": data.hex()})
  return found


def preview(link, item: dict, option_index: int) -> dict:
  before = read_data(link, item)
  after = apply_option(item, before, option_index)
  return {"before": before.hex(), "after": after.hex(), "payload": write_request(item, after, read_mask(link, item)).hex()}


def _write_and_verify(link, item: dict, data: bytes, mask: bytes) -> bool:
  _expect(link, item, write_request(item, data, mask), 0x7B)
  return read_data(link, item) == data


def write(link, item: dict, option_index: int, expected_before: str, backups) -> dict:
  before = read_data(link, item)
  if before.hex() != expected_before.lower():
    raise StaleDataError(f"ECU 当前数据 {before.hex()} 与预览时 {expected_before} 不一致")
  after = apply_option(item, before, option_index)
  mask = read_mask(link, item)
  backup_id = _backup(backups, item, before, mask)
  verified = _write_and_verify(link, item, after, mask)
  return {"backupId": backup_id, "verified": verified, "current": current_option(item, read_data(link, item))}


def _backup(backups, item: dict, data: bytes, mask: bytes) -> str:
  try:
    return backups.save({"uid": item["uid"], "name": item["name"], "tx": item["tx"], "sub": item["sub"],
                         "readId": item["readId"], "writeId": item["writeId"], "data": data.hex(), "mask": mask.hex()})
  except OSError as exc:
    raise BackupError(f"写前备份失败，未写入：{exc}") from exc


def restore(link, backup: dict, backups) -> dict:
  """恢复也是写入：先把 ECU 当前数据另存一份备份，再写回备份数据并回读校验。"""
  item = {**backup, "readOnly": False}
  mask = bytes.fromhex(backup["mask"])
  backup_id = _backup(backups, item, read_data(link, item), mask)
  return {"backupId": backup_id, "verified": _write_and_verify(link, item, bytes.fromhex(backup["data"]), mask)}
