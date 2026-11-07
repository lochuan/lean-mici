"""Sienna 4 代实时数据（规则来自 Carista 逆向，见 spec-v2 §1.6；目录项见 catalog.load_sienna()["live"]；另有 Techstream 第二数据源，由 tools/lanlink/merge_sienna_techstream_items.py 叠加，未上车验证）。

- 全部 UDS 22 DID → 62 DID payload；同一 (ECU, DID) 的项合并成一次请求。
- raw = payload[byteIndex : byteIndex + 掩码字节数] 大端 & mask；signed 按掩码位宽取补码。
- value = offset + mult × raw（全精度，电池页要拿它算块间差）；text 按 precision 保留小数，选项型出标签。
- 失败（无响应、应答异常、负响应、应答太短）只记在该项的 error 上，不影响其他项。
"""
from openpilot.system.lanlinkd.toyota import catalog
from openpilot.system.lanlinkd.toyota.link import ECU_COMMUNICATION_ERRORS, expect_positive, read_error_text


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
  return catalog.option_label(item["options"], value) or f"{value:.{item['precision']}f}"


def _read_did(link, ecu_id: str, did: str) -> bytes:
  try:
    return expect_positive(link, catalog.ecu_address(catalog.ecu(ecu_id)), b"\x22" + bytes.fromhex(did), 3)
  except ECU_COMMUNICATION_ERRORS as exc:
    raise _ReadError(read_error_text(exc)) from None


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
