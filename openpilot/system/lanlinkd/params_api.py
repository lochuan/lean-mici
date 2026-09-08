"""params 读写纯逻辑：黑名单、类型校验、版本计数。store 为 duck-type Params。"""
import json

VERSION_KEY = "LanLinkParamsVersion"

# 继承上游 sunnylinkd BLOCKED_PARAMS（9 项）+ 本地安全项
BLOCKED_PARAMS = {
  "AdbEnabled",
  "CompletedSunnylinkConsentVersion",
  "CompletedTrainingVersion",
  "GithubUsername",       # 可被用于提权 SSH
  "GithubSshKeys",        # 直接 SSH 注入
  "HasAcceptedTerms",
  "HasAcceptedTermsSP",
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


def type_name(raw_type) -> str:
  if hasattr(raw_type, "name"):
    return str(raw_type.name)
  return _TYPE_NAMES.get(int(raw_type), str(raw_type))


def validate_value(type_name_str: str, value: str) -> bool:
  try:
    if type_name_str == "BOOL":
      return value in ("0", "1")
    if type_name_str == "INT":
      int(value)
    elif type_name_str == "FLOAT":
      float(value)
    elif type_name_str == "JSON":
      json.loads(value)
    # STRING / TIME / BYTES 接受任意字符串
  except (ValueError, TypeError):
    return False
  return True


def _bump_version(store) -> None:
  current = store.get(VERSION_KEY) or "0"
  store.put(VERSION_KEY, str(int(current) + 1), block=True)


def list_params(store) -> dict[str, dict]:
  return {key: {"type": type_name(store.get_type(key)), "blocked": key in BLOCKED_PARAMS}
          for key in store.all_keys()}


def read_param(store, key: str) -> tuple[int, str | None]:
  if key in BLOCKED_PARAMS and key != VERSION_KEY:
    # spec §5.3：版本计数网页可读，用于感知车机端改动
    return 403, None
  value = store.get(key)
  if value is None:
    return 404, None
  return 200, value


def write_param(store, key: str, value: str) -> tuple[int, str]:
  if key in BLOCKED_PARAMS:
    return 403, "blocked"
  if key not in store.all_keys():
    return 404, "unknown key"
  if not validate_value(type_name(store.get_type(key)), value):
    return 400, "invalid value for type"
  store.put(key, value, block=True)
  _bump_version(store)
  return 204, ""


def delete_param(store, key: str) -> tuple[int, None]:
  if key in BLOCKED_PARAMS:
    return 403, None
  if key not in store.all_keys():
    return 404, None
  store.remove(key)
  _bump_version(store)
  return 204, None
