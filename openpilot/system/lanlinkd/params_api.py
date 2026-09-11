"""params 读写纯逻辑：黑名单、类型转换、版本计数。store 为 duck-type Params。

AGNOS Params（libparams_c）的类型契约（common/params.py）：
- all_keys() 返回 bytes key 列表（唯一 bytes 边界）
- get() 返回 python 类型值（STRING->str, BOOL->bool, INT->int, JSON->dict/list, ...）
- put() 要求 value 的 python 类型与参数类型匹配（python2cpp 表），否则 TypeError
本模块拥有这个边界：web 层只进出 str。
"""
import json
from datetime import datetime

VERSION_KEY = "LanLinkParamsVersion"

# 继承上游 sunnylinkd BLOCKED_PARAMS（9 项）+ 本地安全项
BLOCKED_PARAMS = {
  "AdbEnabled",
  "GithubUsername",       # 可被用于提权 SSH
  "GithubSshKeys",        # 直接 SSH 注入
  "OnroadCycleRequested",  # 防远程触发循环上电
  "ParamsVersion",         # 设备管理计数
  # LANLink 本地新增
  "AccessToken",           # comma API 凭证
  "AssistNowToken",        # u-blox AssistNow 凭证
  "DoReboot",              # manager 远程电源触发器
  "DoShutdown",
  "DoUninstall",
  "LanLinkEnabled",        # 防本 API 自锁（设备端或 SSH 改）
  "LanLinkPasswordHash",   # 本服务密码哈希
  "LanLinkParamsVersion",  # 本服务写计数
  "SecOCKey",              # 车辆安全密钥
  "SshEnabled",            # SSH 开关
}

_TYPE_NAMES = {0: "STRING", 1: "BOOL", 2: "INT", 3: "FLOAT", 4: "TIME", 5: "JSON", 6: "BYTES"}
_BOOL_ON = ("1", "true", "on")
_BOOL_OFF = ("0", "false", "off")


def type_name(raw_type) -> str:
  if hasattr(raw_type, "name"):
    return str(raw_type.name)
  return _TYPE_NAMES.get(int(raw_type), str(raw_type))


def to_str(x) -> str | None:
  # 类型值统一转 str（key 解码 / 状态快照用）
  if x is None:
    return None
  if isinstance(x, (bytes, bytearray)):
    return bytes(x).decode("utf-8", "replace")
  return str(x)


def coerce_value(type_name_str: str, value: str):
  """UI 字符串 -> C store 需要的 python 类型值；非法返回 None。"""
  if type_name_str == "STRING":
    return value
  if type_name_str == "BOOL":
    v = value.strip().lower()
    if v in _BOOL_ON:
      return True
    if v in _BOOL_OFF:
      return False
    return None
  if type_name_str == "INT":
    try:
      return int(value.strip())
    except ValueError:
      return None
  if type_name_str == "FLOAT":
    try:
      return float(value.strip())
    except ValueError:
      return None
  if type_name_str == "JSON":
    try:
      parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
      return None
    # python2cpp 只接受 (dict|list, JSON)；标量 JSON 会让 C store 抛 TypeError
    return parsed if isinstance(parsed, (dict, list)) else None
  if type_name_str == "TIME":
    try:
      return datetime.fromisoformat(value.strip())
    except ValueError:
      return None
  if type_name_str == "BYTES":
    return value.encode("utf-8")
  return None


def _value_to_api(value, type_name_str: str) -> str:
  """C store 类型值 -> API 规范字符串（UI 可直接回写）。"""
  if value is None:
    return ""
  if type_name_str == "BOOL":
    return "1" if value else "0"
  if type_name_str in ("INT", "FLOAT"):
    return str(value)
  if type_name_str == "JSON":
    return json.dumps(value)
  if type_name_str == "TIME":
    return value.isoformat() if isinstance(value, datetime) else str(value)
  if isinstance(value, (bytes, bytearray)):
    return bytes(value).decode("utf-8", "replace")
  return str(value)


def _bump_version(store) -> None:
  # LanLinkParamsVersion 是 INT 类型：get 返回 python int，put 也要 int
  current = store.get(VERSION_KEY)
  try:
    n = int(current) if current is not None else 0
  except (TypeError, ValueError):
    n = 0
  store.put(VERSION_KEY, n + 1, block=True)


def _all_str_keys(store) -> set[str]:
  return {to_str(k) for k in store.all_keys()}


def list_params(store) -> dict[str, dict]:
  return {to_str(key): {"type": type_name(store.get_type(key)), "blocked": to_str(key) in BLOCKED_PARAMS}
          for key in store.all_keys()}


def read_all(store) -> dict[str, str]:
  out = {}
  for key in store.all_keys():
    k = to_str(key)
    if k in BLOCKED_PARAMS:
      continue
    value = store.get(key)
    if value is not None:
      out[k] = _value_to_api(value, type_name(store.get_type(key)))
      continue
    # 未设置过的 BOOL 要显式报成 "0"，不能整条省略。
    # 设备侧到处用 params.get_bool()，它把"未设置"当 False（common/params.py），
    # 所以 "0" 才是这个 param 的真实语义。省略的话前端分不清"关"和"不存在"：
    # 本机 17/80 个 schema key 就是未设置状态，其中 ExperimentalMode、
    # EnforceTorqueControl 还控制着整个子面板的进入条件。
    if type_name(store.get_type(key)) == "BOOL":
      out[k] = "0"
  return out


def read_param(store, key: str) -> tuple[int, str | None]:
  if key in BLOCKED_PARAMS and key != VERSION_KEY:
    # spec §5.3：版本计数网页可读，用于感知车机端改动
    return 403, None
  # 必须先查 key 是否存在，不能依赖 get() 返回 None：真实 Params.get() 对未知 key
  # 抛 UnknownKeyName（common/params.py check_key），只有 fake store 才返回 None。
  # 少了这一行，GET /api/params/<未知key> 在设备上是 500 而非 404。
  if key not in _all_str_keys(store):
    return 404, None
  value = store.get(key)
  if value is None:
    # key 存在但没设过值。BOOL 报 "0"（与 get_bool 的语义一致，也与 read_all 一致）；
    # 其他类型没有可推导的默认值，仍按"无内容"处理。
    if type_name(store.get_type(key)) == "BOOL":
      return 200, "0"
    return 404, None
  return 200, _value_to_api(value, type_name(store.get_type(key)))


def write_param(store, key: str, value: str) -> tuple[int, str]:
  if key in BLOCKED_PARAMS:
    return 403, "blocked"
  if key not in _all_str_keys(store):
    return 404, "unknown key"
  tn = type_name(store.get_type(key))
  typed = coerce_value(tn, value)
  if typed is None:
    return 400, "invalid value for type"
  store.put(key, typed, block=True)
  _bump_version(store)
  return 204, ""


def delete_param(store, key: str) -> tuple[int, None]:
  if key in BLOCKED_PARAMS:
    return 403, None
  if key not in _all_str_keys(store):
    return 404, None
  store.remove(key)
  _bump_version(store)
  return 204, None
