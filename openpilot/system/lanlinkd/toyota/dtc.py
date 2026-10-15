"""Sienna 4 代故障码读/清（规则来自 Carista 逆向，见 spec-v2 §1.5）。

- UDS ECU：19 02 8D → 59 02 <可用掩码> 后每 4 字节 = 3 字节码 + 1 字节状态，码全 0 跳过；清 10 03 → 14 FF FF FF → 54（默认会话下多数 ECU 回 7F 14 11），
  个别 ECU 先/后试 OBD 清码 04 → 44（顺序见 CLEAR_OBD_FIRST / CLEAR_OBD_FALLBACK）。
- Toyota CAN ECU（车窗类，对 19 回 7F 19 11）：13 <pid>，pid 依次试 81、80、B0，取第一条正响应；
  53 后 1 字节个数 N + N×2 字节码，没有状态；清 14 → 54。
- 码串：首字节高 2 位 → PCBU，其余照抄十六进制；UDS 码再加 -FTB（如 U0131-87）。
- 无响应 / 应答异常 / 负响应都按 ECU 记下返回，不抛出。
"""
from openpilot.system.lanlinkd.toyota import catalog
from openpilot.system.lanlinkd.toyota.link import (ECU_COMMUNICATION_ERRORS, EXTENDED_SESSION, EcuRejectedError, MalformedResponseError,
                                                   NoResponseError, expect_positive, read_error_text)

UDS_READ = bytes.fromhex("19028D")
CAN_READ_PIDS = (0x81, 0x80, 0xB0)
CAN_CLEAR = b"\x14"
UDS_CLEAR = bytes.fromhex("14FFFFFF")
OBD_CLEAR = b"\x04"
# 清码顺序来自 GTS 元数据里各 ECU 类别的 dtc_clear 定义：多数 UDS ECU 先 10 03 再 14 FFFFFF，下面几个例外
CLEAR_OBD_FIRST = {"700", "724", "7D2", "7B0", "7C4"}   # 先 04，失败再 10 03 + 14 FFFFFF
CLEAR_OBD_FALLBACK = {"705"}                            # 先 10 03 + 14 FFFFFF，失败再 04
NO_CODE = b"\x00\x00\x00"


def decode_code(raw: bytes) -> str:
  code = "PCBU"[raw[0] >> 6] + str((raw[0] >> 4) & 0x3) + raw[:2].hex().upper()[1:]
  return f"{code}-{raw[2]:02X}" if len(raw) == 3 else code


def describe(code: str, dtc_text: dict) -> str:
  """「基码说明（FTB 说明）」；基码查不到返回空串（前端显示「暂无中文说明」）。"""
  text = dtc_text["codes"].get(code[:5])
  if text is None:
    return ""
  ftb = dtc_text["ftb"].get(code[6:]) if len(code) > 5 else None
  return text + (f"（{ftb}）" if ftb else "")


def _parse_uds(payload: bytes) -> list[tuple[str, int | None]]:
  records = payload[1:]   # 跳过状态可用掩码
  return [(decode_code(records[i:i + 3]), records[i + 3]) for i in range(0, len(records) - 3, 4)
          if records[i:i + 3] != NO_CODE]


def _parse_can(payload: bytes) -> list[tuple[str, int | None]]:
  count, records = payload[0], payload[1:]
  return [(decode_code(records[i * 2:i * 2 + 2]), None) for i in range(min(count, len(records) // 2))]


def _read_codes(link, ecu: dict) -> tuple[list[tuple[str, int | None]] | None, str]:
  """返回 (码列表, 错误)；码列表为 None 表示 ECU 拒绝了所有读码请求。
  所有请求都无响应才抛 NoResponseError（某个 pid 不应答不影响试下一个）。"""
  address = catalog.ecu_address(ecu)
  if ecu["protocol"] == "uds":
    attempts = [(UDS_READ, 2, _parse_uds)]
  else:
    attempts = [(bytes([0x13, pid]), 1, _parse_can) for pid in CAN_READ_PIDS]
  rejection = None
  for request, echo_len, parse in attempts:
    try:
      payload = expect_positive(link, address, request, echo_len)
    except NoResponseError:
      continue
    except EcuRejectedError as exc:
      rejection = rejection or exc
      continue
    if payload:
      return parse(payload), ""
    rejection = rejection or EcuRejectedError(request, b"")
  if rejection is None:
    raise NoResponseError(f"{ecu['id']} 读故障码无响应")
  return None, read_error_text(rejection)


def scan_ecu(link, ecu: dict, dtc_text: dict) -> dict:
  """读一个 ECU 的故障码：{ecu, status: offline / malformed / ok, protocol, dtcs, readError?, malformedReason?}。"""
  report = {"ecu": ecu["id"], "status": "ok", "protocol": ecu["protocol"], "dtcs": []}
  try:
    codes, error = _read_codes(link, ecu)
  except NoResponseError:
    return {**report, "status": "offline"}
  except MalformedResponseError as exc:
    return {**report, "status": "malformed", "malformedReason": str(exc)}
  if codes is None:
    return {**report, "readError": error}
  for code, status in codes:
    report["dtcs"].append({"code": code, "status": status, "text": describe(code, dtc_text)})
  return report


def _clear_uds(link, address) -> None:
  expect_positive(link, address, EXTENDED_SESSION, 2)
  expect_positive(link, address, UDS_CLEAR, 1)


def _clear_obd(link, address) -> None:
  expect_positive(link, address, OBD_CLEAR, 1)


def clear(link, ecu: dict) -> dict:
  """按 GTS 的顺序依次尝试清码，任一成功即 ok；全失败返回最后一次的错误。"""
  address = catalog.ecu_address(ecu)
  if ecu["protocol"] == "toyota_can":
    attempts = [lambda: expect_positive(link, address, CAN_CLEAR, 1)]
  elif ecu["id"] in CLEAR_OBD_FIRST:
    attempts = [lambda: _clear_obd(link, address), lambda: _clear_uds(link, address)]
  elif ecu["id"] in CLEAR_OBD_FALLBACK:
    attempts = [lambda: _clear_uds(link, address), lambda: _clear_obd(link, address)]
  else:
    attempts = [lambda: _clear_uds(link, address)]
  for attempt in attempts:
    try:
      attempt()
    except ECU_COMMUNICATION_ERRORS as exc:
      error = exc
    else:
      return {"ecu": ecu["id"], "ok": True}
  return {"ecu": ecu["id"], "ok": False, "error": read_error_text(error)}
