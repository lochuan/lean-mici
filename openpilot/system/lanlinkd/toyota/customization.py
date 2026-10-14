"""Toyota 定制项的纯逻辑（复刻 Car Scanner CustomizableCodingTemplate + ToyotaA8Item）。

- A8 03 → E8 03 后跟若干条 [id, len, mask[len]]：每个本地 ID 哪些位可写。
- 21 id → 61 id <data>：data 只含掩码非零的字节（压缩形式），BIT:n:b 的 n 是它的下标。
- 写：改位 → 按掩码展开（掩码 0 的位置填 0）→ 3B id <展开数据>，期望 7B id。
"""
from openpilot.system.lanlinkd.toyota import coding
from openpilot.system.lanlinkd.toyota.link import MalformedResponseError, NoResponseError

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


def _probe_masks(link, address: tuple[int, int | None]) -> tuple[dict[int, bytes], dict]:
  """3E 探测 → A8 03 掩码表。返回 (掩码表, 明细)，明细里的 status 说明为什么这个 ECU 没有可定制项。"""
  try:
    link.request(*address, b"\x3e")
  except NoResponseError:
    return {}, {"status": "offline"}
  try:
    response = link.request(*address, b"\xa8\x03")
  except NoResponseError:
    return {}, {"status": "silent"}
  except MalformedResponseError as exc:
    return {}, {"status": "malformed", "malformedReason": str(exc)}
  masks = parse_masks(response)
  if masks:
    return masks, {"status": "ok", "masks": len(masks)}
  if response[:1] == b"\x7f":
    return {}, {"status": "rejected", "nrc": f"{response[2]:02X}" if len(response) >= 3 else ""}
  return {}, {"status": "empty", "response": response.hex()}


def _read_error_text(exc: Exception) -> str:
  if isinstance(exc, NoResponseError):
    return "无响应"
  if isinstance(exc, MalformedResponseError):
    return "应答异常"
  return f"NRC {exc.nrc:02X}" if getattr(exc, "nrc", None) is not None else "应答格式不符"


def scan(link, items: list[dict]) -> dict:
  """探测 ECU → A803 掩码 → 读出支持项的当前值。

  返回 {"items": 读到当前值的项, "ecus": 每个 ECU 的明细}。列表为空时明细能说明卡在哪一步：
  offline（3E 无响应）/ silent（A803 无响应）/ rejected（负响应 nrc）/ empty（正响应但没有掩码）/
  malformed（A803 应答不符合 ISO-TP，malformedReason 是原因）/ ok。
  """
  masks_by_ecu: dict[tuple, dict[int, bytes]] = {}
  reports: dict[tuple, dict] = {}
  data_cache: dict[tuple, bytes | None] = {}
  found = []
  for item in items:
    address = _address(item)
    if address not in masks_by_ecu:
      masks_by_ecu[address], detail = _probe_masks(link, address)
      reports[address] = {"tx": item["tx"], "sub": item["sub"], "total": 0, "supported": 0, "read": 0, **detail}
    report = reports[address]
    report["total"] += 1
    if not is_supported(item, masks_by_ecu[address]):
      continue
    report["supported"] += 1
    key = (address, item["readId"])
    if key not in data_cache:
      try:
        data_cache[key] = read_data(link, item)
      except (NoResponseError, MalformedResponseError, EcuRejectedError) as exc:
        data_cache[key] = None
        report.setdefault("readError", _read_error_text(exc))
    data = data_cache[key]
    if data is None:
      continue
    report["read"] += 1
    found.append({**item, "current": current_option(item, data), "raw": data.hex()})
  return {"items": found, "ecus": list(reports.values())}


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


# ---- Sienna 4 代：按 ECU 扫描（目录见 catalog.load_sienna，位运算见 coding.py）----

def ecu_address(ecu: dict) -> tuple[int, int | None]:
  return int(ecu["tx"], 16), int(ecu["sub"], 16) if ecu["sub"] else None


def _positive(link, address: tuple[int, int | None], request: bytes, echo_len: int) -> bytes:
  """发请求，要求正响应（SID+0x40 且回显请求的前 echo_len 字节），返回回显之后的数据。"""
  response = link.request(*address, request)
  expected = bytes([request[0] + 0x40]) + request[1:echo_len]
  if response[:echo_len] != expected:
    raise EcuRejectedError(request, response)
  return response[echo_len:]


def read_uds_did(link, address: tuple[int, int | None], did: int) -> bytes:
  return _positive(link, address, b"\x22" + did.to_bytes(2, "big"), 3)


def read_can_expanded(link, address: tuple[int, int | None], local_id: int, mask_entry: bytes) -> bytes:
  return coding.expand(_positive(link, address, bytes([0x21, local_id]), 2), mask_entry)


class _UdsReader:
  """一个 UDS ECU 的扫描：支持位图按 DID 高字节缓存，每个 DID 只读一次。"""

  def __init__(self, link, address: tuple[int, int | None]):
    self.link, self.address = link, address
    self._bitmaps: dict[int, bytes] = {}
    self._payloads: dict[int, bytes | Exception] = {}

  def supported(self, setting: dict) -> bool:
    did = int(setting["did"], 16)
    high = did >> 8
    if high not in self._bitmaps:
      try:
        self._bitmaps[high] = read_uds_did(self.link, self.address, high << 8)
      except (NoResponseError, MalformedResponseError, EcuRejectedError):
        self._bitmaps[high] = b""
    return coding.uds_did_supported(self._bitmaps[high], did)

  def payload(self, setting: dict) -> bytes:
    did = int(setting["did"], 16)
    if did not in self._payloads:
      try:
        self._payloads[did] = read_uds_did(self.link, self.address, did)
      except (NoResponseError, MalformedResponseError, EcuRejectedError) as exc:
        self._payloads[did] = exc
    if isinstance(self._payloads[did], Exception):
      raise self._payloads[did]
    return self._payloads[did]


class _CanReader:
  """一个 Toyota CAN ECU 的扫描：A8 03 掩码表读一次，每个本地 ID 只读一次。"""

  def __init__(self, link, address: tuple[int, int | None]):
    self.link, self.address = link, address
    self.masks = coding.parse_masks(link.request(*address, b"\xa8\x03"))
    self._data: dict[int, bytes | Exception] = {}

  def supported(self, setting: dict) -> bool:
    entry = self.masks.get(int(setting["localId"], 16), b"")
    return coding.can_setting_supported(entry, setting["byteIndex"], int(setting["mask"], 16))

  def payload(self, setting: dict) -> bytes:
    local_id = int(setting["localId"], 16)
    if local_id not in self._data:
      try:
        self._data[local_id] = read_can_expanded(self.link, self.address, local_id, self.masks[local_id])
      except (NoResponseError, MalformedResponseError, EcuRejectedError) as exc:
        self._data[local_id] = exc
    if isinstance(self._data[local_id], Exception):
      raise self._data[local_id]
    return self._data[local_id]


def _bit_support_ok(setting: dict, payload: bytes) -> bool:
  """requiresBitSupportCheck：前一字节同样位置的位是「该项存在」的标志。"""
  mask = int(setting["mask"], 16)
  index = setting["byteIndex"] - 1
  return 0 <= index < len(payload) and payload[index] & mask == mask


def scan_ecu(link, ecu: dict, settings: list[dict]) -> dict:
  """读一个 ECU 上目录里各定制项的当前值。

  返回 {ecu, status, items: [{key, value, raw}], total, supported, read, readError?, malformedReason?}；
  status：offline（3E 无响应）/ malformed（应答不符合 ISO-TP）/ ok。raw 是 UDS 的整段 payload 或 CAN 的展开数据。
  """
  address = ecu_address(ecu)
  report = {"ecu": ecu["id"], "status": "ok", "items": [], "total": len(settings), "supported": 0, "read": 0}
  try:
    link.request(*address, b"\x3e")
    reader = _CanReader(link, address) if ecu["protocol"] == "toyota_can" else _UdsReader(link, address)
  except NoResponseError:
    return {**report, "status": "offline"}
  except MalformedResponseError as exc:
    return {**report, "status": "malformed", "malformedReason": str(exc)}
  for setting in settings:
    if not reader.supported(setting):
      continue
    try:
      payload = reader.payload(setting)
    except (NoResponseError, MalformedResponseError, EcuRejectedError) as exc:
      report["supported"] += 1
      report.setdefault("readError", _read_error_text(exc))
      continue
    if setting["requiresBitSupportCheck"] and not _bit_support_ok(setting, payload):
      continue
    report["supported"] += 1
    try:
      value = coding.extract_value(payload, setting["byteIndex"], int(setting["mask"], 16))
    except ValueError:
      report.setdefault("readError", "应答太短")
      continue
    report["read"] += 1
    report["items"].append({"key": setting["key"], "value": value, "raw": payload.hex()})
  return report
