"""Actuator layer tests: frame codec, MockActuator, BluetoothActuator, GattTransport.

Frame layout per spec §7 (field list is authoritative; the spec's "8 字节" label
miscounts — [op][seq][button][mode][count][u16][u16] is 9 bytes). UUIDs, opcodes
and button/mode bytes come from constants.py (Task 2) — never redefined here.
"""

import struct
from queue import Queue
from types import SimpleNamespace

import pytest

from jeepney.low_level import HeaderFields, MessageType

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.actuator import (
  ActuatorState,
  BluetoothActuator,
  MockActuator,
  PressCommand,
  decode_status,
  encode_abort,
  encode_command,
)
from openpilot.selfdrive.cruisebuttond.ble import GattTransport


def _cmd(**overrides):
  base = {"button": "accel", "mode": "tap", "count": 3, "interval_s": 0.8, "duration_s": 0.18, "seq": 1}
  base.update(overrides)
  return PressCommand(**base)


# ---------------------------------------------------------------- frame codec

def test_encode_command_frame_layout():
  frame = encode_command(_cmd(seq=0x2A))
  op, seq, button, mode, count, duration_ms, interval_ms = struct.unpack("<BBBBBHH", frame)
  assert op == C.OP_PRESS
  assert seq == 0x2A
  assert button == C.BUTTON_RES_UP
  assert mode == C.MODE_TAP
  assert count == 3
  assert duration_ms == 180
  assert interval_ms == 800


def test_encode_command_decel_hold_forces_single_press():
  frame = encode_command(_cmd(button="decel", mode="hold", count=7, interval_s=0.8, duration_s=2.0))
  op, _seq, button, mode, count, duration_ms, interval_ms = struct.unpack("<BBBBBHH", frame)
  assert op == C.OP_PRESS
  assert button == C.BUTTON_RES_DOWN
  assert mode == C.MODE_HOLD
  assert count == 1  # 长按恒 1 拍
  assert interval_ms == 0
  assert duration_ms == 2000


def test_encode_command_wraps_seq_to_u8():
  frame = encode_command(_cmd(seq=300))
  assert struct.unpack("<BBBBBHH", frame)[1] == 300 % 256


def test_encode_abort_frame():
  frame = encode_abort(seq=9)
  assert len(frame) == 9
  op, seq, button, mode, count, duration_ms, interval_ms = struct.unpack("<BBBBBHH", frame)
  assert op == C.OP_ABORT
  assert seq == 9
  assert (button, mode, count, duration_ms, interval_ms) == (0, 0, 0, 0, 0)


def test_decode_status_round_trip():
  frame = struct.pack("<BBBB", 7, C.BT_EXECUTING, 2, 1)
  assert decode_status(frame) == (7, C.BT_EXECUTING, 2, 1)


def test_decode_status_rejects_bad_length():
  with pytest.raises(ValueError):
    decode_status(b"\x01\x02\x03")


def test_encode_rejects_unknown_button_and_mode():
  with pytest.raises(ValueError):
    encode_command(_cmd(button="left"))
  with pytest.raises(ValueError):
    encode_command(_cmd(mode="smash"))


# ---------------------------------------------------------------- MockActuator

def test_mock_records_commands_and_defaults_connected():
  mock = MockActuator()
  cmd = _cmd(seq=1)
  mock.press(cmd)
  assert mock.commands == [cmd]
  assert mock.state() == ActuatorState(connected=True)


def test_mock_ack_transitions_per_spec():
  mock = MockActuator()
  mock.press(_cmd(seq=5, count=3, interval_s=0.8, duration_s=0.18))
  mock.poll(0.1)  # burst 开始 -> executing ACK,尚无拍完成
  st = mock.state()
  assert st.executing and st.pending == 3 and st.last_seq == 5
  mock.poll(0.2)  # 第 1 拍(0.18s)完成
  assert mock.state().pending == 2 and mock.state().executing
  mock.poll(1.0)  # 第 2 拍(0.98s)完成
  assert mock.state().pending == 1
  mock.poll(1.8)  # 第 3 拍(1.78s)完成 -> idle ACK
  st = mock.state()
  assert not st.executing and st.pending == 0 and st.last_seq == 5 and not st.fault


def test_mock_latency_delays_ack():
  mock = MockActuator(latency_s=0.5)
  mock.press(_cmd(seq=1, mode="hold", count=1, duration_s=1.0, interval_s=0.0))
  mock.poll(0.2)
  st = mock.state()
  assert not st.executing and st.pending == 0  # ACK 未到,状态保持
  mock.poll(0.6)
  assert mock.state().executing


def test_mock_abort_clears_pending():
  mock = MockActuator()
  mock.press(_cmd(seq=2, mode="hold", count=1, duration_s=5.0, interval_s=0.0))
  mock.poll(0.1)
  assert mock.state().executing
  mock.abort()
  st = mock.state()
  assert not st.executing and st.pending == 0
  assert mock.abort_calls == 1


def test_mock_fault_injection_drops_presses():
  mock = MockActuator()
  mock.inject_fault()
  assert mock.state().fault
  mock.press(_cmd(seq=3, count=2, interval_s=0.8, duration_s=0.18))
  mock.poll(10.0)
  st = mock.state()
  assert len(mock.commands) == 1  # 记录了,但从不执行
  assert not st.executing and st.pending == 0
  mock.clear_fault()
  assert not mock.state().fault
  mock.press(_cmd(seq=4, count=1, interval_s=0.8, duration_s=0.18))
  mock.poll(10.1)  # burst 进行中(完成时刻 10.18)
  assert mock.state().executing


# ----------------------------------------------------------- BluetoothActuator

class FakeTransport:
  def __init__(self):
    self.connected = False
    self.frames = []
    self.calls = []
    self.fail_write = False
    self.fail_connect = False

  def connect(self):
    self.calls.append("connect")
    if self.fail_connect:
      raise RuntimeError("org.bluez.Error.Failed")
    self.connected = True

  def notify(self):
    self.calls.append("notify")
    self.connected = True

  def notify_stop(self):
    self.calls.append("notify_stop")

  def write(self, data):
    self.calls.append(("write", data))
    if self.fail_write:
      raise RuntimeError("write failed")

  def drain(self):
    out, self.frames = self.frames, []
    return out

  def disconnect(self):
    self.calls.append("disconnect")
    self.connected = False


def _bluetooth(transport=None):
  transport = transport if transport is not None else FakeTransport()
  return BluetoothActuator(address="AA:BB:CC:DD:EE:FF", transport_factory=lambda: transport), transport


def test_bluetooth_press_dropped_when_disconnected():
  act = BluetoothActuator()  # 未配置地址:永不连接
  act.press(_cmd(seq=1))     # 绝不抛异常
  act.abort()
  st = act.state()
  assert not st.connected and not st.executing and st.pending == 0
  assert act.dropped == 1


def test_bluetooth_connect_then_write_command_frame():
  act, t = _bluetooth()
  act.poll(0.0)
  assert t.calls == ["connect", "notify"]
  assert act.state().connected
  cmd = _cmd(seq=9, count=2, interval_s=0.8, duration_s=0.18)
  act.press(cmd)
  assert t.calls[-1] == ("write", encode_command(cmd))


def test_bluetooth_status_frames_update_state():
  act, t = _bluetooth()
  act.poll(0.0)
  t.frames = [
    struct.pack("<BBBB", 9, C.BT_EXECUTING, 2, 3),
    struct.pack("<BBBB", 9, C.BT_IDLE, 0, 3),
  ]
  act.poll(1.0)
  st = act.state()
  assert not st.executing and st.pending == 0 and st.last_seq == 9 and not st.fault
  t.frames = [struct.pack("<BBBB", 10, C.BT_FAULT, 0, 3)]
  act.poll(2.0)
  assert act.state().fault


def test_bluetooth_write_failure_disconnects_and_drops():
  act, t = _bluetooth()
  act.poll(0.0)
  t.fail_write = True
  act.press(_cmd(seq=3))  # 绝不抛异常
  assert not act.state().connected
  assert act.dropped == 1
  t.fail_write = False
  act.poll(0.1)  # 掉线后立即重试
  assert act.state().connected
  act.press(_cmd(seq=4))
  assert t.calls[-1] == ("write", encode_command(_cmd(seq=4)))


def test_bluetooth_abort_only_writes_when_connected():
  act, t = _bluetooth()
  act.abort()  # 未连接:静默忽略
  assert t.calls == []
  act.poll(0.0)
  act.abort()
  assert t.calls[-1] == ("write", encode_abort(seq=0))


def test_bluetooth_connect_retry_backoff():
  act, t = _bluetooth()
  t.fail_connect = True
  act.poll(0.0)
  assert not act.state().connected
  act.poll(1.0)  # 退避窗口内:不重复尝试
  assert t.calls.count("connect") == 1
  t.fail_connect = False
  act.poll(10.0)  # 窗口已过:重试成功
  assert t.calls.count("connect") == 2
  assert act.state().connected


# ---------------------------------------------------------------- GattTransport

ADDRESS = "11:22:33:44:55:66"
DEVICE_PATH = "/org/bluez/hci0/dev_11_22_33_44_55_66"
SERVICE_PATH = DEVICE_PATH + "/service0020"
COMMAND_PATH = SERVICE_PATH + "/char0021"
STATUS_PATH = SERVICE_PATH + "/char0024"


def _objects():
  return {
    DEVICE_PATH: {
      "org.bluez.Device1": {"Address": ("s", ADDRESS), "Connected": ("b", False)},
      "org.bluez.Adapter1": {"Address": ("s", "00:00:00:00:00:00")},
    },
    SERVICE_PATH: {"org.bluez.GattService1": {"UUID": ("s", C.BLE_SERVICE_UUID)}},
    COMMAND_PATH: {
      "org.bluez.GattCharacteristic1": {"UUID": ("s", C.BLE_COMMAND_UUID), "Service": ("o", SERVICE_PATH)}
    },
    STATUS_PATH: {
      "org.bluez.GattCharacteristic1": {"UUID": ("s", C.BLE_STATUS_UUID), "Service": ("o", SERVICE_PATH)}
    },
  }


class FakeRouter:
  """Records jeepney method calls; synthesizes replies (FakeBlueZ 模式)."""

  def __init__(self, objects=None):
    self.objects = objects if objects is not None else _objects()
    self.calls = []  # (path, interface, member, signature, body)
    self.fail_on = set()
    self.filters = []
    self.closed = False

  def send_and_get_reply(self, message, timeout=15.0):
    fields = message.header.fields
    path = fields.get(HeaderFields.path, "/")
    interface = fields.get(HeaderFields.interface, "")
    member = fields.get(HeaderFields.member, "")
    self.calls.append((path, interface, member, fields.get(HeaderFields.signature), message.body))
    if member in self.fail_on:
      header = SimpleNamespace(message_type=MessageType.error, fields={HeaderFields.error_name: "org.bluez.Error.Failed"})
      return SimpleNamespace(header=header, body=("boom",))
    header = SimpleNamespace(message_type=MessageType.method_return, fields={})
    body = (self.objects,) if member == "GetManagedObjects" else ()
    return SimpleNamespace(header=header, body=body)

  def filter(self, rule, *, queue=None, bufsize=1):
    handle = SimpleNamespace(rule=rule, queue=queue if queue is not None else Queue(maxsize=bufsize), closed=False)
    handle.close = lambda: setattr(handle, "closed", True)
    self.filters.append(handle)
    return handle

  def close(self):
    self.closed = True


def _transport(router):
  return GattTransport(
    C.BLE_SERVICE_UUID, C.BLE_COMMAND_UUID, C.BLE_STATUS_UUID, ADDRESS,
    dbus_factory=lambda: router,
  )


def test_gatt_connect_resolves_device_and_characteristics():
  router = FakeRouter()
  t = _transport(router)
  t.connect()
  assert t.connected
  assert [call[2] for call in router.calls] == ["GetManagedObjects", "Connect", "GetManagedObjects"]
  assert router.calls[1][:3] == (DEVICE_PATH, "org.bluez.Device1", "Connect")
  assert t.command_path == COMMAND_PATH
  assert t.status_path == STATUS_PATH


def test_gatt_notify_subscribes_properties_changed_before_start():
  router = FakeRouter()
  t = _transport(router)
  t.connect()
  t.notify()
  rule = router.filters[0].rule
  assert rule.header_fields == {
    "interface": "org.freedesktop.DBus.Properties",
    "member": "PropertiesChanged",
    "path": STATUS_PATH,
  }
  assert router.calls[-1][:3] == (STATUS_PATH, "org.bluez.GattCharacteristic1", "StartNotify")


def test_gatt_write_sends_frame_with_options_dict():
  router = FakeRouter()
  t = _transport(router)
  t.connect()
  t.write(b"\x00\x01")
  call = router.calls[-1]
  assert call[:3] == (COMMAND_PATH, "org.bluez.GattCharacteristic1", "WriteValue")
  assert call[3] == "aya{sv}"
  assert call[4] == (b"\x00\x01", {})


def test_gatt_drain_returns_status_frames():
  router = FakeRouter()
  t = _transport(router)
  t.connect()
  t.notify()
  queue = router.filters[0].queue
  frame = struct.pack("<BBBB", 3, C.BT_EXECUTING, 1, 2)
  queue.put(SimpleNamespace(body=("org.bluez.GattCharacteristic1", {"Value": ("ay", frame)}, [])))
  queue.put(SimpleNamespace(body=("org.bluez.GattCharacteristic1", {"Connected": ("b", True)}, [])))
  assert t.drain() == [frame]  # 无 Value 的 PropertiesChanged 被忽略
  assert t.drain() == []


def test_gatt_notify_stop_and_disconnect_sequence():
  router = FakeRouter()
  t = _transport(router)
  t.connect()
  t.notify()
  t.disconnect()
  assert router.filters[0].closed
  assert [call[2] for call in router.calls][-2:] == ["StopNotify", "Disconnect"]
  assert router.calls[-1][:3] == (DEVICE_PATH, "org.bluez.Device1", "Disconnect")
  assert router.closed and not t.connected


def test_gatt_connect_error_raises():
  router = FakeRouter()
  router.fail_on = {"Connect"}
  t = _transport(router)
  with pytest.raises(RuntimeError):
    t.connect()
  assert not t.connected


def test_gatt_unknown_address_raises():
  router = FakeRouter(objects={})
  t = _transport(router)
  with pytest.raises(RuntimeError):
    t.connect()
