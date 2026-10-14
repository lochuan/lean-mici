"""Sienna 4 代定制项：按 ECU 扫描、写入、恢复（规则来自 Carista 逆向，位运算见 coding.py）。

- UDS ECU：22 XX00 支持位图 → 22 DID 读整段 payload；写 = 读改写 2E DID，7F 2E 7F 时切 10 03 重试一次。
- Toyota CAN ECU（车窗类）：A8 03 掩码表 → 21 id 读压缩数据并按掩码展开；写 3B id <展开数据>，期望 7B id。
- 写入一律先备份、写后回读校验；ECU 协议写死在目录里（catalog.load_sienna）。
"""
from openpilot.system.lanlinkd.toyota import coding
from openpilot.system.lanlinkd.toyota.link import MalformedResponseError, NoResponseError


class EcuRejectedError(Exception):
  def __init__(self, request: bytes, response: bytes):
    self.nrc = response[2] if len(response) >= 3 and response[0] == 0x7F else None
    super().__init__(f"ECU 拒绝 {request[:2].hex()}：{response.hex()}")


class StaleDataError(Exception):
  """扫描之后 ECU 里的数据变了，拒绝按旧数据写入。"""


class BackupError(Exception):
  """写前备份没存下来（如 /data 不可写）：不写入。"""


def _read_error_text(exc: Exception) -> str:
  if isinstance(exc, NoResponseError):
    return "无响应"
  if isinstance(exc, MalformedResponseError):
    return "应答异常"
  return f"NRC {exc.nrc:02X}" if getattr(exc, "nrc", None) is not None else "应答格式不符"


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


# ---- Sienna 4 代：写入、恢复（读改写 → 回读校验；写前一律备份）----

EXTENDED_SESSION = b"\x10\x03"
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F


def _read_payload(link, address: tuple[int, int | None], ecu: dict, setting: dict) -> bytes:
  if ecu["protocol"] == "toyota_can":
    local_id = int(setting["localId"], 16)
    mask_entry = coding.parse_masks(link.request(*address, b"\xa8\x03")).get(local_id)
    if not mask_entry:
      raise EcuRejectedError(b"\xa8\x03", b"")
    return read_can_expanded(link, address, local_id, mask_entry)
  return read_uds_did(link, address, int(setting["did"], 16))


def _write_payload(link, address: tuple[int, int | None], ecu: dict, setting: dict, payload: bytes) -> None:
  if ecu["protocol"] == "toyota_can":
    _positive(link, address, bytes([0x3B, int(setting["localId"], 16)]) + payload, 2)
    return
  request = b"\x2e" + int(setting["did"], 16).to_bytes(2, "big") + payload
  response = link.request(*address, request)
  if response == bytes([0x7F, 0x2E, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]):
    # 默认会话不让写：按 Carista 的做法切扩展会话再试一次（实测读取在默认会话即可）
    _positive(link, address, EXTENDED_SESSION, 2)
    response = link.request(*address, request)
  if response[:3] != b"\x6e" + request[1:3]:
    raise EcuRejectedError(request, response)


def _save_backup(backups, ecu: dict, setting: dict, data: bytes, after: bytes) -> str:
  try:
    return backups.save({"key": setting["key"], "ecu": ecu["id"], "data": data.hex(), "after": after.hex()})
  except OSError as exc:
    raise BackupError(f"写前备份失败，未写入：{exc}") from exc


def _write_verified(link, address, ecu: dict, setting: dict, before: bytes, after: bytes, backups) -> dict:
  backup_id = _save_backup(backups, ecu, setting, before, after)
  _write_payload(link, address, ecu, setting, after)
  current = _read_payload(link, address, ecu, setting)
  return {"key": setting["key"], "value": coding.extract_value(current, setting["byteIndex"], int(setting["mask"], 16)),
          "verified": current == after, "backupId": backup_id, "before": before.hex(), "after": after.hex()}


def write_setting(link, ecu: dict, setting: dict, value: int, expected_before: str, backups) -> dict:
  """expected_before：扫描时的 raw；ECU 里的数据变了就不写（StaleDataError）。"""
  address = ecu_address(ecu)
  before = _read_payload(link, address, ecu, setting)
  if before.hex() != expected_before.lower():
    raise StaleDataError(f"ECU 当前数据 {before.hex()} 与扫描时 {expected_before} 不一致")
  after = coding.insert_value(before, setting["byteIndex"], int(setting["mask"], 16), value)
  return _write_verified(link, address, ecu, setting, before, after, backups)


def restore_setting(link, ecu: dict, setting: dict, backup: dict, backups) -> dict:
  """恢复也是写入：先把当前数据另存一份备份，再写回备份数据并回读校验；原备份标为已恢复。"""
  address = ecu_address(ecu)
  current = _read_payload(link, address, ecu, setting)
  result = _write_verified(link, address, ecu, setting, current, bytes.fromhex(backup["data"]), backups)
  backups.mark_restored(backup["id"])
  return result
