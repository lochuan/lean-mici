"""Sienna 4 代实时数据（规则来自 Carista 逆向，见 spec-v2 §1.6；目录项见 catalog.load_sienna()["live"]）。

- 全部 UDS 22 DID → 62 DID payload；同一 (ECU, DID) 的项合并成一次请求。
- raw = payload[byteIndex : byteIndex + 掩码字节数] 大端 & mask；signed 按掩码位宽取补码。
- value = offset + mult × raw（全精度，电池页要拿它算块间差）；text 按 precision 保留小数，选项型出标签。
- 失败（无响应、应答异常、负响应、应答太短）只记在该项的 error 上，不影响其他项。
"""
from openpilot.system.lanlinkd.toyota import catalog
from openpilot.system.lanlinkd.toyota.link import MalformedResponseError, NoResponseError


class _ReadError(Exception):
  pass


def decode(item: dict, payload: bytes) -> float:
  mask = int(item["mask"], 16)
  width = len(item["mask"]) // 2
  chunk = payload[item["byteIndex"]:item["byteIndex"] + width]
  if len(chunk) < width:
    raise _ReadError("应答太短")
  raw = int.from_bytes(chunk, "big") & mask
  bits = mask.bit_length()
  if item["signed"] and raw >> (bits - 1):
    raw -= 1 << bits
  return item["offset"] + item["mult"] * raw


def format_value(item: dict, value: float) -> str:
  label = next((o["label"] for o in item["options"] or [] if o["value"] == value), None)
  return label if label is not None else f"{value:.{item['precision']}f}"


def _read_did(link, ecu_id: str, did: str) -> bytes:
  request = b"\x22" + bytes.fromhex(did)
  try:
    response = link.request(*catalog.ecu_address(catalog.ecu(ecu_id)), request)
  except NoResponseError:
    raise _ReadError("无响应") from None
  except MalformedResponseError:
    raise _ReadError("应答异常") from None
  if response[:3] == b"\x62" + request[1:3]:
    return response[3:]
  if len(response) >= 3 and response[0] == 0x7F:
    raise _ReadError(f"NRC {response[2]:02X}")
  raise _ReadError("应答格式不符")


def read(link, items: list[dict]) -> list[dict]:
  """按 items 顺序返回 [{key, value, text}]；失败的项 value 为 None、text 为空，并带 error。"""
  payloads: dict[tuple[str, str], bytes | _ReadError] = {}
  values = []
  for item in items:
    source = (item["ecu"], item["did"])
    if source not in payloads:
      try:
        payloads[source] = _read_did(link, *source)
      except _ReadError as exc:
        payloads[source] = exc
    try:
      if isinstance(payloads[source], _ReadError):
        raise payloads[source]
      value = decode(item, payloads[source])
    except _ReadError as exc:
      values.append({"key": item["key"], "value": None, "text": "", "error": str(exc)})
      continue
    values.append({"key": item["key"], "value": value, "text": format_value(item, value)})
  return values
