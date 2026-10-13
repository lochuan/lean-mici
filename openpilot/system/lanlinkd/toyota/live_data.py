"""实时数据：按 Car Scanner 自定义 PID 定义读 ECU、解码（profiles7 的 Toyota 配置，见 data/pid_profiles.json）。

求值语义（对照反编译 CustomPID.cs / OBDDataReader.cs 确认）：
- data = 正响应去掉 SID 和回显：cmd 以 22 开头去 3 字节（62 + DID），其他去 len(cmd) 字节（2104 → 去 61 04）。
  所有下标从 0 起、相对 data。
- type 0 公式：A..Z、AA..FZ 依次是 data[0..181]，与 sbi/dl/mul/div/ofs 无关（见 formula.py）。
- type 1 字节组：data[sbi:sbi+dl] 大端（dl 为 0 则取到末尾，最多 4 字节），signed 按 dl 位宽补码；
  值 = raw * mul / div + ofs，div 为 0 当 1。
- type 2 位值：data[sbi] 的第 bit 位（0 = 最低位），值 0/1，不缩放。
- type 3 是执行器动作（如强制 DPF 再生），一律不提供。
- 只保留 11 位 CAN（3 位十六进制 hdr）的 PID：29 位（DA62E0…）和 K 线（812AF0）在 comma 线束上用不了，
  hdr 为空的 01xx 走 7DF 广播，也不做。cmd 为空的是计算值（只引用别的 PID）。
- 同一 (hdr, sub, cmd) 的 PID 合并成一次请求。
"""
import re

from openpilot.system.lanlinkd.toyota.formula import FormulaError, evaluate
from openpilot.system.lanlinkd.toyota.link import NoResponseError

PROFILE_2016_ON = "96260a504ebb4c6781eedee86dba6f09"
PROFILE_2009_2015 = "726eab54fe6f4247a5b0c88d7f9e8174"
PROFILE_UP_TO_2008 = "7bc899de79a34bf98596ea8e6c6f27b4"

# VIN 第 10 位年份码：1-9 = 2001-2009，A-Y（跳过 I O Q U Z）= 2010-2030
_YEAR_CODES = {c: 2001 + i for i, c in enumerate("123456789")} | {c: 2010 + i for i, c in enumerate("ABCDEFGHJKLMNPRSTVWXY")}
_LETTERS = [chr(ord("A") + i) for i in range(26)]
BYTE_NAMES = {name: i for i, name in enumerate(_LETTERS + [a + b for a in "ABCDEF" for b in _LETTERS])}
_PID_REF = re.compile(r"PID\s*\(\s*(\d+)\s*\)", re.IGNORECASE)
TYPE_FORMULA, TYPE_BYTES, TYPE_BIT = 0, 1, 2


class DecodeError(Exception):
  pass


def default_profile_id(vin: str) -> str:
  year = _YEAR_CODES.get(vin[9:10].upper()) if len(vin) == 17 else None
  if year is None or year >= 2016:
    return PROFILE_2016_ON
  return PROFILE_2009_2015 if year >= 2009 else PROFILE_UP_TO_2008


def _references(item: dict) -> list[int]:
  return [int(i) for i in _PID_REF.findall(item["formula"])] if item["type"] == TYPE_FORMULA else []


def readable_pids(pids: list[dict]) -> list[dict]:
  direct = {p["id"] for p in pids if (p["type"] in (TYPE_BYTES, TYPE_BIT) or (p["type"] == TYPE_FORMULA and p["formula"]))
            and re.fullmatch(r"[0-9A-Fa-f]{3}", p["hdr"]) and p["cmd"]}
  return [p for p in pids if p["id"] in direct
          or (not p["hdr"] and not p["cmd"] and p["type"] == TYPE_FORMULA and _references(p)
              and all(r in direct for r in _references(p)))]


def response_data(cmd: str, response: bytes) -> bytes | None:
  request = bytes.fromhex(cmd)
  echo = 3 if request[0] == 0x22 else len(request)
  expected = bytes([request[0] + 0x40]) + request[1:echo]
  return response[echo:] if response[:echo] == expected else None


def decode(item: dict, data: bytes, pid_values: dict[int, float]) -> float:
  sbi = item["sbi"]
  if item["type"] == TYPE_FORMULA:
    def byte_value(name: str) -> int:
      return data[BYTE_NAMES[name]]
    try:
      return evaluate(item["formula"], byte_value, pid_values.__getitem__)
    except FormulaError as exc:
      raise DecodeError(str(exc)) from exc
  if item["type"] == TYPE_BIT:
    if sbi >= len(data):
      raise DecodeError("响应太短")
    return (data[sbi] >> item["bit"]) & 1
  length = min(item["dl"] or len(data) - sbi, 4)
  if length <= 0 or len(data) < sbi + length:
    raise DecodeError("响应太短")
  raw = int.from_bytes(data[sbi:sbi + length], "big", signed=item["signed"])
  return raw * item["mul"] / (item["div"] or 1) + item["ofs"]


def format_value(value: float) -> str:
  return f"{value:.2f}".rstrip("0").rstrip(".")


def _request_key(item: dict) -> tuple[str, str | None, str]:
  return item["hdr"].upper(), item["sub"], item["cmd"].upper()


def _fetch(link, key: tuple[str, str | None, str]) -> bytes:
  hdr, sub, cmd = key
  try:
    response = link.request(int(hdr, 16), int(sub, 16) if sub else None, bytes.fromhex(cmd))
  except NoResponseError as exc:
    raise DecodeError("ECU 无响应") from exc
  data = response_data(cmd, response)
  if data is None:
    raise DecodeError(f"ECU 拒绝 {cmd}：{response.hex()}")
  return data


def read(link, catalog: dict[int, dict], ids: list[int]) -> list[dict]:
  """catalog：id → PID（应已经过 readable_pids）。每个 id 一条结果，失败的带 error。"""
  wanted = [i for i in ids if i in catalog]
  needed = dict.fromkeys(r for i in wanted for r in _references(catalog[i]))
  responses: dict[tuple, bytes | DecodeError] = {}
  values: dict[int, float] = {}
  errors: dict[int, str] = {}
  # 先解码直接读的（含计算值引用到的），最后算计算值
  for pid_id in sorted(dict.fromkeys(wanted) | needed, key=lambda i: not catalog[i]["cmd"]):
    item = catalog[pid_id]
    try:
      if item["cmd"]:
        key = _request_key(item)
        if key not in responses:
          try:
            responses[key] = _fetch(link, key)
          except DecodeError as exc:
            responses[key] = exc
        if isinstance(responses[key], DecodeError):
          raise responses[key]
        values[pid_id] = decode(item, responses[key], values)
      else:
        values[pid_id] = decode(item, b"", values)
    except DecodeError as exc:
      errors[pid_id] = str(exc)
  out = []
  for pid_id in ids:
    if pid_id in values:
      out.append({"id": pid_id, "value": values[pid_id], "text": format_value(values[pid_id])})
    else:
      out.append({"id": pid_id, "value": None, "text": "", "error": errors.get(pid_id, "未知 PID")})
  return out
