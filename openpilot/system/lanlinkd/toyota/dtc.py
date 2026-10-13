"""Toyota 故障码读/清（复刻 Car Scanner ToyotaCANECU/ToyotaCANGatewayECU + DTCDecoder）。

- 先发 3E 探测：正响应或 7F 3E 都算 ECU 在线，无响应跳过。
- 读：按顺序试读码命令，取第一条正响应。响应布局（去掉正响应 SID 后）：
  13xx → 计数 + 每码 2 字节（无状态字节，Car Scanner 视为「确认」）；
  17/18xx → 计数 + 每码 2 字节 + 1 字节 KWP 状态；
  19 02 → 再跳过子功能回显和状态可用掩码，之后每码 3 字节 + 1 字节 UDS 状态。
  0000 / AAAA 是占位码，跳过。
- 清：依次发清码命令，任一正响应即成功（7F xx 78 由 link 等到最终响应）。
"""
from openpilot.system.lanlinkd.toyota.link import NoResponseError

READ_DIRECT = [bytes.fromhex(c) for c in ("1800FF00", "1802FF00", "18FF00", "17FF00", "13FF00", "13FFFF", "13", "1381",
                                          "1902AC", "190278", "190208", "190FAC")]
READ_GATEWAY = [bytes.fromhex(c) for c in ("13", "1381", "13FF00", "13B0", "1380")]
CLEAR = [bytes.fromhex(c) for c in ("04", "14", "14FF00", "14FFFF", "14FFFFFF")]

# Car Scanner 简体中文资源里的 UDS 状态位名称（bit0..bit7）
UDS_STATUS_BITS = ("测试失败", "在当前里程中测试失败", "暂时", "确认", "最后复位后测试未完成", "最后复位后测试失败",
                   "在当前里程未完成测试", "故障指示灯亮")
PLACEHOLDERS = {b"\x00\x00", b"\xaa\xaa", b"\x00\x00\x00", b"\xaa\xaa\xaa"}


def decode_code(raw: bytes) -> str:
  # 首字节高 2 位 → 字母，接着 2 位 → 第一位数字，其余半字节照抄十六进制
  return "PCBU"[raw[0] >> 6] + str((raw[0] >> 4) & 0x3) + raw.hex().upper()[1:]


def _uds_status(status: int) -> str:
  return "、".join(name for bit, name in enumerate(UDS_STATUS_BITS) if status & (1 << bit)) or f"0x{status:02X}"


def parse_response(request: bytes, response: bytes) -> list[tuple[str, str]] | None:
  """正响应 → [(码, 状态)]；负响应或格式不符 → None。"""
  if not response or response[0] != request[0] + 0x40:
    return None
  if request[0] == 0x19:
    records, code_len, status_of = response[3:], 3, _uds_status
    count = len(records) // 4
  else:
    if len(response) < 2:
      return None
    records, count = response[2:], response[1]
    code_len = 2
    status_of = (lambda s: f"0x{s:02X}") if request[0] in (0x17, 0x18) else None
  size = code_len + (status_of is not None)
  dtcs = []
  for i in range(min(count, len(records) // size)):
    record = records[i * size:(i + 1) * size]
    raw = record[:code_len]
    if raw in PLACEHOLDERS:
      continue
    dtcs.append((decode_code(raw), status_of(record[code_len]) if status_of else "确认"))
  return dtcs


def describe(code: str, descriptions: dict[str, dict[str, str]]) -> str:
  for candidate in dict.fromkeys((code, code[:5])):
    for table in ("toyota", "obd2"):
      if candidate in descriptions[table]:
        return descriptions[table][candidate]
  return ""


def _read_ecu(link, tx: int, sub: int | None) -> list[tuple[str, str]]:
  for command in READ_GATEWAY if sub is not None else READ_DIRECT:
    try:
      dtcs = parse_response(command, link.request(tx, sub, command))
    except NoResponseError:
      continue
    if dtcs is not None:
      return dtcs
  return []


def scan(link, targets: list[dict], descriptions: dict[str, dict[str, str]]) -> list[dict]:
  """targets：{ecu, tx, sub, name}（见 catalog.dtc_targets）。只返回在线的 ECU。"""
  found = []
  for target in targets:
    try:
      link.request(target["tx"], target["sub"], b"\x3e")
    except NoResponseError:
      continue
    dtcs = [{"code": code, "status": status, "description": describe(code, descriptions)}
            for code, status in _read_ecu(link, target["tx"], target["sub"])]
    found.append({"ecu": target["ecu"], "name": target["name"], "dtcs": dtcs})
  return found


def clear(link, target: dict) -> dict:
  error = ""
  for command in CLEAR:
    try:
      response = link.request(target["tx"], target["sub"], command)
    except NoResponseError:
      error = "ECU 无响应"
      continue
    if response and response[0] == command[0] + 0x40:
      return {"ecu": target["ecu"], "ok": True}
    error = f"ECU 拒绝 {command.hex()}：{response.hex()}"
  return {"ecu": target["ecu"], "ok": False, "error": error}
