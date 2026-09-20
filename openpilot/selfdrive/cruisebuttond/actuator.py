"""cruisebuttond 执行器层(spec §7/§8)。

- CruiseButtonActuator:Protocol,下游 Task 5/6(调度器/守护进程)只依赖它。
- MockActuator:全链路测试用 —— 记录命令、可配置 ACK 延迟、可注入 fault,
  poll(now) 驱动 ACK 状态迁移。
- BluetoothActuator:委托 GattTransport(ble.py,jeepney/BlueZ GATT)。
  未连接时 press 静默丢弃(绝不抛异常),连接失败按退避重试。

帧编解码为纯函数:命令帧 9 字节(字段列表为准,spec §7 的"8 字节"标签
少算 —— 上车验证项 6 与固件对齐时确认)、状态帧 4 字节。
"""

import dataclasses
import struct
from collections.abc import Callable
from functools import partial
from typing import Protocol

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.ble import GattTransport


# 断连后的重连退避(20Hz poll 下避免 D-Bus 轰炸)
CONNECT_RETRY_S = 5.0

_FRAME = struct.Struct("<BBBBBHH")  # [op][seq][button][mode][count][duration_ms u16][interval_ms u16]
_STATUS = struct.Struct("<BBBB")    # [seq][state][pending][fw]

_BUTTONS = {"accel": C.BUTTON_RES_UP, "decel": C.BUTTON_RES_DOWN}
_MODES = {"tap": C.MODE_TAP, "hold": C.MODE_HOLD}


@dataclasses.dataclass(frozen=True)
class PressCommand:
  button: str        # "accel" | "decel"
  mode: str          # "tap" | "hold"
  count: int         # tap 的拍数;hold 恒 1
  interval_s: float  # tap 拍间隔;hold 为 0
  duration_s: float  # hold 持续;tap 恒 TAP_PRESS_MS/1000
  seq: int


@dataclasses.dataclass
class ActuatorState:
  connected: bool = False
  executing: bool = False
  pending: int = 0
  last_seq: int = 0
  fault: bool = False


class CruiseButtonActuator(Protocol):
  def press(self, cmd: PressCommand) -> None: ...
  def abort(self) -> None: ...
  def state(self) -> ActuatorState: ...
  def poll(self, now: float) -> None: ...  # 每帧驱动(处理 ACK/notify)


def encode_command(cmd: PressCommand) -> bytes:
  """PressCommand -> 命令帧(spec §7)。长按强制 count=1、interval=0。"""
  try:
    button = _BUTTONS[cmd.button]
    mode = _MODES[cmd.mode]
  except KeyError as error:
    raise ValueError(f"unknown button/mode: {error}") from None
  hold = mode == C.MODE_HOLD
  return _FRAME.pack(
    C.OP_PRESS, cmd.seq & 0xFF, button, mode,
    1 if hold else cmd.count,
    int(round(cmd.duration_s * 1000)),
    0 if hold else int(round(cmd.interval_s * 1000)),
  )


def encode_abort(seq: int = 0) -> bytes:
  """abort 帧:立即释放按钮并清空在途(spec §7 op=1)。seq 回带最近命令序号。"""
  return _FRAME.pack(C.OP_ABORT, seq & 0xFF, 0, 0, 0, 0, 0)


def decode_status(frame: bytes) -> tuple[int, int, int, int]:
  """状态帧 -> (seq, state, pending, fw)。长度不符即 ValueError。"""
  if len(frame) != _STATUS.size:
    raise ValueError(f"status frame must be {_STATUS.size} bytes, got {len(frame)}")
  return _STATUS.unpack(bytes(frame))


class MockActuator:
  """全链路测试用执行器:记录命令;可配置延迟;可注入 fault。

  poll(now) 驱动 ACK 状态迁移(照 spec §7:burst 开始 -> executing,
  每拍完成 pending 递减,全部完成 -> idle)。ACK 未到时状态保持不变,
  与真实 BLE 语义一致(central 只能靠 notify 更新)。新 press 替换在途 burst。
  """

  def __init__(self, latency_s: float = 0.0):
    self.commands: list[PressCommand] = []
    self.abort_calls = 0
    self.latency_s = latency_s
    self._fault = False
    self._burst: tuple[PressCommand, float, tuple[float, ...]] | None = None  # (cmd, start, completions)
    self._state = ActuatorState(connected=True)
    self._now = 0.0

  def press(self, cmd: PressCommand) -> None:
    self.commands.append(cmd)
    if self._fault:
      return  # 故障注入:记录但从不执行
    start = self._now + self.latency_s
    completions = tuple(start + cmd.duration_s + i * cmd.interval_s for i in range(cmd.count))
    self._burst = (cmd, start, completions)

  def abort(self) -> None:
    self.abort_calls += 1
    self._burst = None
    self._state.executing = False
    self._state.pending = 0

  def inject_fault(self) -> None:
    self._fault = True
    self._burst = None
    self._state.fault = True
    self._state.executing = False
    self._state.pending = 0

  def clear_fault(self) -> None:
    self._fault = False
    self._state.fault = False

  def poll(self, now: float) -> None:
    self._now = now
    if self._fault or self._burst is None:
      return
    cmd, start, completions = self._burst
    if now < start:
      return
    completed = sum(1 for t in completions if now >= t)
    self._state.last_seq = cmd.seq
    if completed >= cmd.count:
      self._burst = None
      self._state.executing = False
      self._state.pending = 0
    else:
      self._state.executing = True
      self._state.pending = cmd.count - completed

  def state(self) -> ActuatorState:
    return dataclasses.replace(self._state)


class BluetoothActuator:
  """GattTransport 工厂注入;未配对/连接失败 -> connected=False,绝不抛。

  press 在未连接时静默丢弃并计数(dropped);写失败视为掉线,下帧立即重连。
  状态只由状态帧 notify 驱动(poll 时 drain)。
  """

  def __init__(self, address: str = "", transport_factory: Callable[[], GattTransport] | None = None):
    self._address = address
    if transport_factory is None:
      transport_factory = partial(GattTransport, C.BLE_SERVICE_UUID, C.BLE_COMMAND_UUID, C.BLE_STATUS_UUID, address)
    self._transport_factory = transport_factory
    self._transport: GattTransport | None = None
    self._state = ActuatorState()
    self._next_connect = 0.0
    self.dropped = 0

  def press(self, cmd: PressCommand) -> None:
    if not self._state.connected or self._transport is None:
      self.dropped += 1
      return
    try:
      self._transport.write(encode_command(cmd))
    except Exception:
      self._drop_connection()
      self.dropped += 1

  def abort(self) -> None:
    if not self._state.connected or self._transport is None:
      return
    self._state.executing = False
    self._state.pending = 0
    try:
      self._transport.write(encode_abort(seq=self._state.last_seq))
    except Exception:
      self._drop_connection()

  def state(self) -> ActuatorState:
    return dataclasses.replace(self._state)

  def poll(self, now: float) -> None:
    if not self._state.connected:
      self._try_connect(now)
      if not self._state.connected:
        return
    try:
      frames = self._transport.drain()
    except Exception:
      self._drop_connection()
      return
    for frame in frames:
      try:
        seq, state, pending, _fw = decode_status(frame)
      except ValueError:
        continue
      self._state.last_seq = seq
      self._state.executing = state == C.BT_EXECUTING
      self._state.pending = pending
      self._state.fault = state == C.BT_FAULT

  def close(self) -> None:
    transport, self._transport = self._transport, None
    self._state = ActuatorState()
    if transport is not None:
      try:
        transport.disconnect()
      except Exception:
        pass

  def _try_connect(self, now: float) -> None:
    if not self._address or now < self._next_connect:
      return
    self._next_connect = now + CONNECT_RETRY_S
    transport = None
    try:
      transport = self._transport_factory()
      transport.connect()
      transport.notify()
    except Exception:
      self._state.connected = False
      if transport is not None:
        try:
          transport.disconnect()
        except Exception:
          pass
      return
    self._transport = transport
    self._state.connected = True

  def _drop_connection(self) -> None:
    self._state = ActuatorState()
    self._next_connect = 0.0  # 下帧立即重试
    transport, self._transport = self._transport, None
    if transport is not None:
      try:
        transport.disconnect()
      except Exception:
        pass
