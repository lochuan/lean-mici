"""CAN 帧级的假 panda + 假 Toyota ECU，供 toyota 诊断测试共用。

FakePanda 实现 DiagLink 用到的 panda 接口（can_send/can_recv/set_safety_mode/
send_heartbeat/close），并把 ISO-TP 帧还原成请求交给 FakeEcu，
再把响应按 ISO-TP 分段发回——包括 0x750 网关的子地址首字节和流控帧，
这样协议模块的测试走的是和真车一样的帧路径。
"""
from collections.abc import Callable


class FakeEcu:
  """按请求字节查表应答。responses 的值可以是 bytes，或 bytes 列表（依次发出，用于 7F xx 78）。"""

  def __init__(self, responses: dict[bytes, bytes | list[bytes]] | None = None,
               handler: Callable[[bytes], bytes | list[bytes] | None] | None = None):
    self.responses = dict(responses or {})
    self.handler = handler
    self.requests: list[bytes] = []

  def respond(self, request: bytes) -> list[bytes]:
    self.requests.append(request)
    reply = self.handler(request) if self.handler else self.responses.get(request)
    if reply is None:
      return []
    return reply if isinstance(reply, list) else [reply]


class FakePanda:
  def __init__(self, ecus: dict[tuple[int, int | None], FakeEcu] | None = None, bus: int = 0):
    self.ecus = dict(ecus or {})
    self.bus = bus
    self.sent: list[tuple[int, bytes, int]] = []
    self.safety_modes: list[tuple[int, int]] = []
    self.heartbeats = 0
    self.closed = False
    self._rx: list[tuple[int, bytes, int]] = []
    self._partial: dict[tuple[int, int | None], tuple[int, bytearray]] = {}   # 收到首帧还没收齐的请求
    self._pending_tx: dict[tuple[int, int | None], list[bytes]] = {}          # 等 tester 流控的响应后续帧

  # ---- panda 接口 ----
  def set_safety_mode(self, mode, param=0):
    self.safety_modes.append((mode, param))

  def send_heartbeat(self, engaged=True):
    self.heartbeats += 1

  def close(self):
    self.closed = True

  def can_recv(self):
    out, self._rx = self._rx, []
    return out

  def can_send(self, addr: int, dat: bytes, bus: int, **kwargs):
    dat = bytes(dat)
    assert len(dat) == 8, "ELM327 safety 只放行 8 字节帧"
    self.sent.append((addr, dat, bus))
    if bus != self.bus:
      return
    key, frame = self._route(addr, dat)
    if key is None:
      return
    kind = frame[0] >> 4
    if kind == 0:    # 单帧
      self._deliver(key, frame[1:1 + (frame[0] & 0x0F)])
    elif kind == 1:  # 首帧：回流控，攒数据
      length = ((frame[0] & 0x0F) << 8) | frame[1]
      self._partial[key] = (length, bytearray(frame[2:]))
      self._emit(key, bytes([0x30, 0x00, 0x00]))
    elif kind == 2:  # 续帧
      length, buf = self._partial[key]
      buf += frame[1:]
      if len(buf) >= length:
        del self._partial[key]
        self._deliver(key, bytes(buf[:length]))
    elif kind == 3:  # tester 的流控：把排队的续帧一次发完
      for f in self._pending_tx.pop(key, []):
        self._emit(key, f)

  # ---- 内部 ----
  def _route(self, addr: int, dat: bytes):
    if (addr, None) in self.ecus:
      return (addr, None), dat
    key = (addr, dat[0])
    if key in self.ecus:
      return key, dat[1:]
    return None, dat

  def _deliver(self, key, request: bytes):
    for reply in self.ecus[key].respond(request):
      self._send_isotp(key, reply)

  def _send_isotp(self, key, payload: bytes):
    max_len = 8 if key[1] is None else 7
    if len(payload) < max_len:
      self._emit(key, bytes([len(payload)]) + payload)
      return
    first = bytes([0x10 | (len(payload) >> 8), len(payload) & 0xFF]) + payload[:max_len - 2]
    rest = payload[max_len - 2:]
    chunks = [rest[i:i + max_len - 1] for i in range(0, len(rest), max_len - 1)]
    self._pending_tx[key] = [bytes([0x20 | ((i + 1) & 0x0F)]) + c for i, c in enumerate(chunks)]
    self._emit(key, first)

  def _emit(self, key, frame: bytes):
    tx, sub = key
    data = (bytes([sub]) if sub is not None else b"") + frame
    self._rx.append((tx + 8, data.ljust(8, b"\x00"), self.bus))
