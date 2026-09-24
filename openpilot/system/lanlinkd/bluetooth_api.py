# system/lanlinkd/bluetooth_api.py
"""蓝牙设置页的纯逻辑（对应 the_galaxy 的 /api/bluetooth/*）。

状态真身在 bluetooth_managerd（unix socket RPC，见
sunnypilot/system/bluetooth/daemon.py）。这里每个请求新建 BluetoothClient：
call() 本就每次开新 socket，无连接态可守。

web 层在 daemon 之外的职责：
  - offroad-only 操作拦 409：daemon 侧 _require_offroad 是纵深防御，
    两层集合保持一致（OFFROAD_ONLY 的 url 名 == daemon.OFFROAD_COMMANDS）。
    connect/disconnect/select_audio 刻意不在集合里——行车中想断开设备或
    换音频输出是合理操作。
  - payload 提取与校验：坏请求在 web 层早失败，不惊动 daemon。
  - daemon 挂了也要能打开页面：降级成 params 推导的 503 快照
    （与 the_galaxy 的降级行为一致）。
"""

from openpilot.sunnypilot.system.bluetooth import BluetoothClient
from openpilot.system.lanlinkd.params_api import to_str

# the_galaxy 同款：daemon 首次 status 可能要拉起 bluez，10s 起步
BLUETOOTH_TIMEOUT = 10.0

# url 操作名 -> daemon 命令名（前端只见 url 名，不见命令名）
OPERATIONS = {
  "power": "set_power",
  "scan": "start_scan",
  "stop_scan": "stop_scan",
  "pair": "pair",
  "connect": "connect",
  "disconnect": "disconnect",
  "forget": "forget",
  "select_audio": "select_audio",
  "test_audio": "test_audio",
  "pairing_response": "pairing_response",
}

OFFROAD_ONLY = frozenset({"power", "scan", "stop_scan", "pair", "forget", "test_audio", "pairing_response"})


def fallback_snapshot(store, error: Exception | str) -> dict:
  """daemon 不可达时的 503 快照：params 能推出多少算多少，页面仍可渲染。"""
  return {
    "available": False,
    "enabled": store.get_bool("BluetoothEnabled"),
    "offroad": store.get_bool("IsOffroad"),
    "selected_audio": to_str(store.get("BluetoothAudioAddress")) or "",
    "devices": [],
    "error": str(error),
  }


def build_payload(command: str, body: dict) -> tuple[int, str, dict]:
  """从请求体提取 daemon 命令的 payload。返回 (http_code, message, payload)，
  code == 0 表示成功。规则与 the_galaxy 一致：
    - set_power 取 enabled；
    - pairing_response 取 prompt_id/accepted/value 三件套；
    - 扫描不需要参数；
    - 其余按 address，且必须非空——唯独 select_audio 例外（空地址 = 取消选择）。
  """
  if command == "set_power":
    return 0, "", {"enabled": bool(body.get("enabled", False))}
  if command == "pairing_response":
    return (
      0,
      "",
      {
        "prompt_id": str(body.get("prompt_id", "")),
        "accepted": bool(body.get("accepted", False)),
        "value": str(body.get("value", "")),
      },
    )
  if command in ("start_scan", "stop_scan"):
    return 0, "", {}
  address = str(body.get("address", ""))
  if not address and command != "select_audio":
    return 400, "Bluetooth device address is required.", {}
  return 0, "", {"address": address}


def status_payload(client, store) -> tuple[int, dict]:
  """GET /api/bluetooth 的响应体。client 为 duck-type BluetoothClient。"""
  try:
    return 200, BluetoothClient.serialize_status(client.status())
  except Exception as error:
    return 503, fallback_snapshot(store, error)


def run_operation(client, store, operation: str, body: dict) -> tuple[int, dict]:
  """POST /api/bluetooth/<operation> 的响应体。

  顺序：404 未知操作 → 409 offroad → 400 payload → 执行。
  校验全部通过后才碰 client，所以假 client 的 calls 里只会出现合法命令。
  """
  command = OPERATIONS.get(operation)
  if command is None:
    return 404, {"error": "Unknown Bluetooth operation."}
  if operation in OFFROAD_ONLY and not store.get_bool("IsOffroad"):
    return 409, {"error": "Bluetooth settings can only be changed offroad."}

  code, message, payload = build_payload(command, body)
  if code:
    return code, {"error": message}

  try:
    if command == "set_power":
      # set_power 走专用方法：开启时负责引导 daemon 起来（写 param + 等 socket）
      client.set_power(payload["enabled"])
      result = {}
    else:
      result = client.call(command, **payload)
    return 200, {"message": "Bluetooth operation started.", **result}
  except Exception as error:
    return 503, {"error": str(error)}
