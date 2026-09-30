# system/lanlinkd/wifi_api.py
"""WiFi 设置页的纯逻辑（lanlink 连接面板的 /api/wifi/*）。

状态真身在 WifiManager（system DBus / NetworkManager，见
system/ui/lib/wifi_manager.py）。lanlinkd 进程里持有一个
WifiManager(manage_tethering=False) 单例，由 lanlinkd.py 懒加载；
本模块只负责 payload 校验与响应构造，与 bluetooth_api.py 同构：

  - offroad-only 操作集合拦 409（connect/static/forget；读状态不拦）。
  - 坏请求在 web 层早失败，不碰 DBus。
"""

import ipaddress
import re

WIFI_TIMEOUT = 15.0

# url 操作名；操作语义见 WifiManager 对应方法
# activate = 已保存 profile 直接激活（无需重输密码，与 connect 的 forget+重建不同）
OPERATIONS = frozenset({"connect", "activate", "static", "forget"})

OFFROAD_ONLY = frozenset({"connect", "activate", "static", "forget"})

MAX_DNS_SERVERS = 3

# NetworkManager 单个 SSID profile；与 mici UI 共用同一份 NM 配置
MAX_SSID_LEN = 64
MAX_PSK_LEN = 63
MIN_PSK_LEN = 8


class WifiValidationError(ValueError):
  pass


def _validate_ipv4(value: str, what: str) -> str:
  try:
    addr = ipaddress.IPv4Address(value)
  except (ipaddress.AddressValueError, ValueError) as exc:
    raise WifiValidationError(f"invalid {what}: {value!r}") from exc
  return str(addr)


def validate_static_config(body: dict) -> dict:
  """校验 POST /api/wifi/<op> 携带的静态配置，返回规范化后的字段。

  只做纯校验，不碰 DBus。字段：
    - ip / gateway: 点分 IPv4
    - prefix: 0..32
    - dns: 1..3 个点分 IPv4（method=manual 没有 DHCP 可以兜底，空 DNS
      会静默断网，所以必须显式提供）
  """
  ip = _validate_ipv4(str(body.get("ip", "")), "ip")

  try:
    prefix = int(body.get("prefix", 0))
  except (TypeError, ValueError) as exc:
    raise WifiValidationError(f"invalid prefix: {body.get('prefix')!r}") from exc
  if not 0 <= prefix <= 32:
    raise WifiValidationError(f"prefix out of range: {prefix}")

  gateway = _validate_ipv4(str(body.get("gateway", "")), "gateway")

  raw_dns = body.get("dns") or []
  if isinstance(raw_dns, str):
    raw_dns = [part.strip() for part in re.split(r"[,\s]+", raw_dns) if part.strip()]
  if not isinstance(raw_dns, list):
    raise WifiValidationError("dns must be a list of IPv4 addresses")
  if len(raw_dns) > MAX_DNS_SERVERS:
    raise WifiValidationError(f"too many DNS servers (max {MAX_DNS_SERVERS})")
  dns = [_validate_ipv4(str(d), "dns server") for d in raw_dns]
  if not dns:
    raise WifiValidationError("at least one DNS server is required in static mode")

  return {"ip": ip, "prefix": prefix, "gateway": gateway, "dns": dns}


def validate_connect_body(body: dict) -> tuple[int, str, dict]:
  """POST /api/wifi/connect 的 payload 校验。返回 (code, message, payload)。"""
  ssid = str(body.get("ssid", "")).strip()
  if not ssid:
    return 400, "SSID is required.", {}
  if len(ssid.encode("utf-8")) > 32 * 4:  # NM 上限 32 字节，给 utf-8 留余量
    return 400, "SSID too long.", {}
  password = str(body.get("password", ""))
  if password and len(password) < 8:
    return 400, "WPA password too short (min 8).", {}
  hidden = bool(body.get("hidden", False))

  static = body.get("static")
  if static is None:
    return 0, "", {"ssid": ssid, "password": password, "hidden": hidden}
  try:
    cfg = validate_static_config(static if isinstance(static, dict) else {})
  except WifiValidationError as exc:
    return 400, str(exc), {}
  return 0, "", {"ssid": ssid, "password": password, "hidden": hidden, "static": cfg}


def _dbus_val(value):
  """Unwrap a jeepney ('s', value) style tuple from GetSettings output."""
  if isinstance(value, tuple) and len(value) == 2:
    return value[1]
  return value


def _addr_entry_to_dict(entry):
  """address-data 的元素：jeepney 形是 [(key, (sig, val)), ...] 的 list，dict 形直接用。"""
  if isinstance(entry, dict):
    return entry
  if isinstance(entry, (list, tuple)):
    return {k: _dbus_val(v) for k, v in entry}
  return {}


def _ipv4_snapshot(profile: dict) -> dict:
  """从连接 profile 的 ipv4 段蒸馏出页面要展示的字段（值可能是 ('s', v) 形）。"""
  ipv4 = profile.get("ipv4", {})
  out = {"method": str(_dbus_val(ipv4.get("method", "auto"))), "addresses": [], "gateway": "", "dns": []}
  addr_data = _dbus_val(ipv4.get("address-data"))
  if isinstance(addr_data, (list, tuple)):
    for a in addr_data:
      d = _addr_entry_to_dict(a)
      if "address" in d:
        out["addresses"].append(str(d["address"]))
  if ipv4.get("gateway"):
    out["gateway"] = str(_dbus_val(ipv4["gateway"]))
  dns_data = _dbus_val(ipv4.get("dns-data"))
  if isinstance(dns_data, (list, tuple)):
    out["dns"] = [str(_dbus_val(d)) for d in dns_data]
  return out


def fallback_snapshot(error: Exception | str) -> dict:
  """NetworkManager 不可达时的降级快照：页面仍可渲染。"""
  return {
    "available": False,
    "offroad": True,
    "connecting": None,
    "connected": None,
    "ipv4": {"method": "unknown", "addresses": [], "gateway": "", "dns": []},
    "networks": [],
    "error": str(error),
  }
