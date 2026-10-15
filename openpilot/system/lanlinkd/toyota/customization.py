"""Sienna 4 代定制项：按 ECU 扫描、写入、恢复（规则来自 Carista 逆向，位运算见 coding.py）。

- UDS ECU：22 XX00 支持位图 → 22 DID 读整段 payload；写 = 读改写 2E DID，7F 2E 7F 时切 10 03 重试一次。
- Toyota CAN ECU（车窗类）：A8 03 掩码表 → 21 id 读压缩数据并按掩码展开；写 3B id <展开数据>，期望 7B id。
- 写入一律先备份、写后回读校验；ECU 协议写死在目录里（catalog.load_sienna）。
"""
from collections.abc import Callable

from openpilot.system.lanlinkd.toyota import catalog, coding
from openpilot.system.lanlinkd.toyota.link import (ECU_COMMUNICATION_ERRORS, EXTENDED_SESSION, EcuRejectedError, MalformedResponseError,
                                                   NoResponseError, expect_positive, read_error_text)

Address = tuple[int, int | None]
READ_CAN_MASK_TABLE = b"\xa8\x03"
NRC_SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F


class StaleDataError(Exception):
  """扫描之后 ECU 里的数据变了，拒绝按旧数据写入。"""


class BackupError(Exception):
  """写前备份没存下来（如 /data 不可写）：不写入。"""


def read_uds_did(link, address: Address, did: int) -> bytes:
  return expect_positive(link, address, b"\x22" + did.to_bytes(2, "big"), 3)


def read_can_expanded(link, address: Address, local_id: int, mask_entry: bytes) -> bytes:
  return coding.expand_by_mask(expect_positive(link, address, bytes([0x21, local_id]), 2), mask_entry)


def _read_once(cache: dict, key, read: Callable[[], bytes]) -> bytes:
  """同一 key 只读一次；读取失败也缓存，之后每次取都重新抛出同一个错误。"""
  if key not in cache:
    try:
      cache[key] = read()
    except ECU_COMMUNICATION_ERRORS as exc:
      cache[key] = exc
  if isinstance(cache[key], Exception):
    raise cache[key]
  return cache[key]


class _UdsReader:
  """一个 UDS ECU 的扫描：支持位图按 DID 高字节缓存，每个 DID 只读一次。"""

  def __init__(self, link, address: Address):
    self.link, self.address = link, address
    self._bitmaps: dict[int, bytes | Exception] = {}
    self._payloads: dict[int, bytes | Exception] = {}

  def supported(self, setting: dict) -> bool:
    did = int(setting["did"], 16)
    high = did >> 8
    bitmap = _read_once(self._bitmaps, high, lambda: read_uds_did(self.link, self.address, high << 8))
    return coding.uds_did_supported(bitmap, did)

  def payload(self, setting: dict) -> bytes:
    did = int(setting["did"], 16)
    return _read_once(self._payloads, did, lambda: read_uds_did(self.link, self.address, did))


class _CanReader:
  """一个 Toyota CAN ECU 的扫描：A8 03 掩码表读一次，每个本地 ID 只读一次。"""

  def __init__(self, link, address: Address):
    self.link, self.address = link, address
    self.masks = coding.parse_masks(link.request(*address, READ_CAN_MASK_TABLE))
    self._data: dict[int, bytes | Exception] = {}

  def supported(self, setting: dict) -> bool:
    entry = self.masks.get(int(setting["localId"], 16), b"")
    return coding.can_setting_supported(entry, setting["byteIndex"], int(setting["mask"], 16))

  def payload(self, setting: dict) -> bytes:
    local_id = int(setting["localId"], 16)
    return _read_once(self._data, local_id, lambda: read_can_expanded(self.link, self.address, local_id, self.masks[local_id]))


def _bit_support_ok(setting: dict, payload: bytes) -> bool:
  """requiresBitSupportCheck：前一字节同样位置的位是「该项存在」的标志。"""
  mask = int(setting["mask"], 16)
  index = setting["byteIndex"] - 1
  return 0 <= index < len(payload) and payload[index] & mask == mask


def scan_ecu(link, ecu: dict, settings: list[dict]) -> dict:
  """读一个 ECU 上目录里各定制项的当前值。

  返回 {ecu, status, items: [{key, value, raw}], total, supported, read, readError?, malformedReason?}；
  status：offline（3E 无响应）/ malformed（应答不符合 ISO-TP）/ ok。raw 是 UDS 的整段 payload 或 CAN 的展开数据。
  readError 是第一个读取失败的原因（支持位图或数据读不到）。
  """
  address = catalog.ecu_address(ecu)
  report = {"ecu": ecu["id"], "status": "ok", "items": [], "total": len(settings), "supported": 0, "read": 0}
  try:
    link.request(*address, b"\x3e")
    reader = _CanReader(link, address) if ecu["protocol"] == "toyota_can" else _UdsReader(link, address)
  except NoResponseError:
    return {**report, "status": "offline"}
  except MalformedResponseError as exc:
    return {**report, "status": "malformed", "malformedReason": str(exc)}
  for setting in settings:
    try:
      if not reader.supported(setting):
        continue
    except ECU_COMMUNICATION_ERRORS as exc:
      report.setdefault("readError", read_error_text(exc))
      continue
    try:
      payload = reader.payload(setting)
    except ECU_COMMUNICATION_ERRORS as exc:
      report["supported"] += 1
      report.setdefault("readError", read_error_text(exc))
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


# ---- 写入、恢复（读改写 → 回读校验；写前一律备份）----

def _read_payload(link, address: Address, ecu: dict, setting: dict) -> bytes:
  if ecu["protocol"] == "toyota_can":
    local_id = int(setting["localId"], 16)
    mask_entry = coding.parse_masks(link.request(*address, READ_CAN_MASK_TABLE)).get(local_id)
    if not mask_entry:
      raise MalformedResponseError(f"A8 03 掩码表里没有本地 ID {local_id:02X}")
    return read_can_expanded(link, address, local_id, mask_entry)
  return read_uds_did(link, address, int(setting["did"], 16))


def _write_payload(link, address: Address, ecu: dict, setting: dict, payload: bytes) -> None:
  if ecu["protocol"] == "toyota_can":
    expect_positive(link, address, bytes([0x3B, int(setting["localId"], 16)]) + payload, 2)
    return
  request = b"\x2e" + int(setting["did"], 16).to_bytes(2, "big") + payload
  response = link.request(*address, request)
  if response == bytes([0x7F, 0x2E, NRC_SERVICE_NOT_SUPPORTED_IN_SESSION]):
    # 默认会话不让写：按 Carista 的做法切扩展会话再试一次（实测读取在默认会话即可）
    expect_positive(link, address, EXTENDED_SESSION, 2)
    response = link.request(*address, request)
  if response[:3] != b"\x6e" + request[1:3]:
    raise EcuRejectedError(request, response)


def _save_backup(backups, ecu: dict, setting: dict, data: bytes, after: bytes) -> str:
  try:
    return backups.save({"key": setting["key"], "ecu": ecu["id"], "data": data.hex(), "after": after.hex()})
  except OSError as exc:
    raise BackupError(f"写前备份失败，未写入：{exc}") from exc


def _write_value(link, ecu: dict, setting: dict, value: int, before: bytes, backups) -> dict:
  """只改 before 里该项的位 → 备份 → 写入 → 回读校验。返回的 after 是回读到的数据（校验失败时 ≠ 打算写入的）。"""
  address = catalog.ecu_address(ecu)
  mask = int(setting["mask"], 16)
  after = coding.insert_value(before, setting["byteIndex"], mask, value)
  backup_id = _save_backup(backups, ecu, setting, before, after)
  _write_payload(link, address, ecu, setting, after)
  current = _read_payload(link, address, ecu, setting)
  return {"key": setting["key"], "value": coding.extract_value(current, setting["byteIndex"], mask),
          "verified": current == after, "backupId": backup_id, "before": before.hex(), "after": current.hex()}


def write_setting(link, ecu: dict, setting: dict, value: int, expected_before: str, backups) -> dict:
  """expected_before：扫描时的 raw；ECU 里的数据变了就不写（StaleDataError）。"""
  before = _read_payload(link, catalog.ecu_address(ecu), ecu, setting)
  if before.hex() != expected_before.lower():
    raise StaleDataError(f"ECU 当前数据 {before.hex()} 与扫描时 {expected_before} 不一致")
  return _write_value(link, ecu, setting, value, before, backups)


def restore_setting(link, ecu: dict, setting: dict, backup: dict, backups) -> dict:
  """撤销 = 把该项的值写回备份时的值（只动该项的位，同一 DID 里后改的其他项不受影响）；回读校验通过才把原备份标为已恢复。"""
  old_value = coding.extract_value(bytes.fromhex(backup["data"]), setting["byteIndex"], int(setting["mask"], 16))
  current = _read_payload(link, catalog.ecu_address(ecu), ecu, setting)
  result = _write_value(link, ecu, setting, old_value, current, backups)
  if result["verified"]:
    backups.mark_restored(backup["id"])
  return result
