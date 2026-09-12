"""bluetooth_api 的纯逻辑测试（仿 test_vehicle_api.py）。

FakeClient 记录收到的命令：run_operation 保证校验全过才碰 client，
所以 calls 里出现的就是 daemon 真正会收到的 payload——顺带锁死了
"web 层与 daemon 命令面一致" 这个契约。

不测真的 unix socket / bluez（那要设备，见 sunnypilot/system/bluetooth/tests）。
"""

from openpilot.sunnypilot.system.bluetooth import BluetoothDevice, BluetoothStatus
from openpilot.system.lanlinkd import bluetooth_api

from .test_models_api import FakeParams


class FakeClient:
  """duck-type BluetoothClient：记录命令，可注入失败。"""

  def __init__(self, error: str = ""):
    self.error = error
    self.calls: list[tuple[str, dict]] = []

  def status(self) -> BluetoothStatus:
    if self.error:
      raise RuntimeError(self.error)
    return BluetoothStatus(
      available=True,
      enabled=True,
      powered=True,
      discovering=True,
      offroad=True,
      selected_audio="AA:BB:CC:DD:EE:FF",
      devices=(BluetoothDevice("AA:BB:CC:DD:EE:FF", "Speaker", paired=True, connected=True, audio=True),),
    )

  def call(self, command, **payload):
    if self.error:
      raise RuntimeError(self.error)
    self.calls.append((command, dict(payload)))
    return {"audio_test_delay_ms": 3000} if command == "test_audio" else {}

  def set_power(self, enabled):
    if self.error:
      raise RuntimeError(self.error)
    self.calls.append(("set_power", {"enabled": enabled}))


def offroad_params(**extra) -> FakeParams:
  return FakeParams({"IsOffroad": True, **extra})


class TestStatusPayload:
  def test_serializes_the_dataclass_shape(self):
    # asdict 递归展开嵌套 dataclass；tuple 保持 tuple（json.dumps 时才变数组）
    code, body = bluetooth_api.status_payload(FakeClient(), FakeParams())
    assert code == 200
    assert body["available"] is True
    assert body["selected_audio"] == "AA:BB:CC:DD:EE:FF"
    assert body["devices"][0]["address"] == "AA:BB:CC:DD:EE:FF"
    assert body["devices"][0]["connected"] is True
    # 嵌套 dataclass 必须真被展开，否则前端拿到的是 repr 字符串
    assert body["devices"][0]["name"] == "Speaker"

  def test_daemon_failure_degrades_to_params_snapshot_with_503(self):
    # daemon 挂了不能让页面打不开：params 能推出多少算多少
    params = FakeParams({"BluetoothEnabled": True, "IsOffroad": False, "BluetoothAudioAddress": b"AA:BB"})
    code, body = bluetooth_api.status_payload(FakeClient(error="socket timeout"), params)
    assert code == 503
    assert body["available"] is False
    assert body["enabled"] is True
    assert body["offroad"] is False
    assert body["devices"] == []
    assert body["error"] == "socket timeout"

  def test_fallback_selected_audio_survives_bytes_params(self):
    # 真实 Params.get 返回 bytes；to_str 边界不能在这里破
    body = bluetooth_api.fallback_snapshot(FakeParams({"BluetoothAudioAddress": b"AA:BB"}), "x")
    assert body["selected_audio"] == "AA:BB"


class TestRunOperation:
  def test_unknown_operation_is_404(self):
    client = FakeClient()
    code, body = bluetooth_api.run_operation(client, offroad_params(), "launch_missiles", {})
    assert code == 404
    assert body["error"]
    assert client.calls == []

  def test_offroad_only_operations_are_409_onroad(self):
    # 集合与 daemon.OFFROAD_COMMANDS 一致；行车中一个都不能放行
    onroad = FakeParams({"IsOffroad": False})
    for operation in sorted(bluetooth_api.OFFROAD_ONLY):
      client = FakeClient()
      code, body = bluetooth_api.run_operation(client, onroad, operation, {"address": "AA:BB"})
      assert code == 409, f"{operation} leaked onroad"
      assert body["error"]
      assert client.calls == []

  def test_connect_disconnect_select_audio_work_onroad(self):
    # 行车中断开 / 换音频是合理操作，不能 409
    onroad = FakeParams({"IsOffroad": False})
    for operation in ("connect", "disconnect", "select_audio"):
      client = FakeClient()
      code, _ = bluetooth_api.run_operation(client, onroad, operation, {"address": "AA:BB"})
      assert code == 200
      assert len(client.calls) == 1

  def test_operation_names_map_to_daemon_commands(self):
    # url 名 != 命令名的映射（power->set_power, scan->start_scan）是最容易错的
    cases = {
      "power": ("set_power", {"enabled": True}),
      "scan": ("start_scan", {}),
      "stop_scan": ("stop_scan", {}),
      "pair": ("pair", {"address": "AA:BB"}),
      "forget": ("forget", {"address": "AA:BB"}),
      "test_audio": ("test_audio", {"address": "AA:BB"}),
    }
    for operation, (command, payload) in cases.items():
      client = FakeClient()
      body = {"address": "AA:BB", "enabled": True}
      code, result = bluetooth_api.run_operation(client, offroad_params(), operation, body)
      assert code == 200
      assert client.calls == [(command, payload)]
      if operation == "test_audio":
        # daemon 的返回（如测试音延迟）要透传给前端做倒计时
        assert result["audio_test_delay_ms"] == 3000

  def test_power_goes_through_the_bootstrap_method(self):
    # set_power 不能走 call()：client.set_power 才有"写 param 等 daemon 起来"的引导
    client = FakeClient()
    code, _ = bluetooth_api.run_operation(client, offroad_params(), "power", {"enabled": True})
    assert code == 200
    assert client.calls == [("set_power", {"enabled": True})]

  def test_pairing_response_payload_extraction(self):
    client = FakeClient()
    code, _ = bluetooth_api.run_operation(client, offroad_params(), "pairing_response", {"prompt_id": 7, "accepted": True, "value": 1234})
    assert code == 200
    # 三件套统一成 str/bool/str，与 daemon agent.respond 的期望一致
    assert client.calls == [("pairing_response", {"prompt_id": "7", "accepted": True, "value": "1234"})]

  def test_missing_address_is_400_without_touching_client(self):
    for operation in ("pair", "connect", "disconnect", "forget", "test_audio"):
      client = FakeClient()
      code, body = bluetooth_api.run_operation(client, offroad_params(), operation, {})
      assert code == 400, f"{operation} without address should be 400"
      assert body["error"]
      assert client.calls == []

  def test_select_audio_allows_empty_address_to_deselect(self):
    client = FakeClient()
    code, _ = bluetooth_api.run_operation(client, offroad_params(), "select_audio", {})
    assert code == 200
    assert client.calls == [("select_audio", {"address": ""})]

  def test_daemon_error_becomes_503_with_error_body(self):
    client = FakeClient(error="Bluetooth is disabled")
    code, body = bluetooth_api.run_operation(client, offroad_params(), "scan", {})
    assert code == 503
    assert body["error"] == "Bluetooth is disabled"


class TestOffroadOnlyMatchesDaemon:
  def test_web_layer_and_daemon_agree_on_the_offroad_set(self):
    # 双层防御的前提是两层集合一致：url 名映射成命令名后必须完全相等
    from openpilot.sunnypilot.system.bluetooth.daemon import OFFROAD_COMMANDS

    web_commands = {bluetooth_api.OPERATIONS[op] for op in bluetooth_api.OFFROAD_ONLY}
    assert web_commands == OFFROAD_COMMANDS
