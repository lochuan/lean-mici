"""Sienna 4 代故障码读/清（规则来自 Carista 逆向，见 spec-v2 §1.5）。

- UDS ECU：19 02 8D → 59 02 <可用掩码> 后每 4 字节 = 3 字节码 + 1 字节状态，码全 0 跳过；清 14 FF FF FF → 54。
- Toyota CAN ECU（车窗类，对 19 回 7F 19 11）：13 <pid>，pid 依次试 81、80、B0，取第一条正响应；
  53 后 1 字节个数 N + N×2 字节码，没有状态；清 14 → 54。
- 码串：首字节高 2 位 → PCBU，其余照抄十六进制；UDS 码再加 -FTB（如 U0131-87）。
- 无响应 / 应答异常 / 负响应都按 ECU 记下返回，不抛出。
"""
from openpilot.system.lanlinkd.toyota import catalog
from openpilot.system.lanlinkd.toyota.link import MalformedResponseError, NoResponseError

UDS_READ = bytes.fromhex("19028D")
CAN_READ_PIDS = (0x81, 0x80, 0xB0)
CLEAR = {"uds": bytes.fromhex("14FFFFFF"), "toyota_can": b"\x14"}
NO_CODE = b"\x00\x00\x00"


def decode_code(raw: bytes) -> str:
  code = "PCBU"[raw[0] >> 6] + str((raw[0] >> 4) & 0x3) + raw[:2].hex().upper()[1:]
  return f"{code}-{raw[2]:02X}" if len(raw) == 3 else code


def describe(code: str, dtc_text: dict) -> tuple[str, bool]:
  """「基码说明（FTB 说明）」和是否推测；基码查不到返回空串（前端显示「暂无中文说明」）。"""
  entry = dtc_text["codes"].get(code[:5])
  if entry is None:
    return "", False
  ftb = dtc_text["ftb"].get(code[6:]) if len(code) > 5 else None
  return entry["text"] + (f"（{ftb}）" if ftb else ""), entry["guess"]


def _parse_uds(payload: bytes) -> list[tuple[str, int | None]]:
  records = payload[1:]   # 跳过状态可用掩码
  return [(decode_code(records[i:i + 3]), records[i + 3]) for i in range(0, len(records) - 3, 4)
          if records[i:i + 3] != NO_CODE]


def _parse_can(payload: bytes) -> list[tuple[str, int | None]]:
  count, records = payload[0], payload[1:]
  return [(decode_code(records[i * 2:i * 2 + 2]), None) for i in range(min(count, len(records) // 2))]


def _nrc_text(response: bytes) -> str:
  return f"NRC {response[2]:02X}" if len(response) >= 3 and response[0] == 0x7F else f"应答格式不符：{response.hex()}"


def _read(link, ecu: dict) -> tuple[list[tuple[str, int | None]] | None, str]:
  """返回 (码列表, 错误)；码列表为 None 表示 ECU 拒绝了所有读码请求。"""
  address = catalog.ecu_address(ecu)
  if ecu["protocol"] == "uds":
    attempts = [(UDS_READ, 2, _parse_uds)]
  else:
    attempts = [(bytes([0x13, pid]), 1, _parse_can) for pid in CAN_READ_PIDS]
  error = ""
  for request, echo_len, parse in attempts:
    response = link.request(*address, request)
    if response[:echo_len] == bytes([request[0] + 0x40]) + request[1:echo_len] and len(response) > echo_len:
      return parse(response[echo_len:]), ""
    error = _nrc_text(response)
  return None, error


def scan_ecu(link, ecu: dict, dtc_text: dict) -> dict:
  """读一个 ECU 的故障码：{ecu, status: offline / malformed / ok, protocol, dtcs, readError?, malformedReason?}。"""
  report = {"ecu": ecu["id"], "status": "ok", "protocol": ecu["protocol"], "dtcs": []}
  try:
    codes, error = _read(link, ecu)
  except NoResponseError:
    return {**report, "status": "offline"}
  except MalformedResponseError as exc:
    return {**report, "status": "malformed", "malformedReason": str(exc)}
  if codes is None:
    return {**report, "readError": error}
  for code, status in codes:
    text, guess = describe(code, dtc_text)
    report["dtcs"].append({"code": code, "status": status, "text": text, "guess": guess})
  return report


def clear(link, ecu: dict) -> dict:
  request = CLEAR[ecu["protocol"]]
  try:
    response = link.request(*catalog.ecu_address(ecu), request)
  except NoResponseError:
    return {"ecu": ecu["id"], "ok": False, "error": "无响应"}
  except MalformedResponseError:
    return {"ecu": ecu["id"], "ok": False, "error": "应答异常"}
  if response[:1] == b"\x54":
    return {"ecu": ecu["id"], "ok": True}
  return {"ecu": ecu["id"], "ok": False, "error": _nrc_text(response)}
