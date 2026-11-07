"""CAN 帧级的假 panda + 假 Toyota ECU，供 toyota 诊断测试共用。

FakePanda 实现 DiagLink 用到的 panda 接口（can_send/can_recv/set_safety_mode/
send_heartbeat/close），并把 ISO-TP 帧还原成请求交给 FakeEcu，
再把响应按 ISO-TP 分段发回——包括 0x750 网关的子地址首字节和流控帧，
这样协议模块的测试走的是和真车一样的帧路径。
"""
import json
from collections.abc import Callable


class FakeEcu:
  """按请求字节查表应答。responses 的值可以是 bytes，或 bytes 列表（依次发出，用于 7F xx 78）。
  misnumbered_frames：多帧应答的续帧序号从 2 起跳（实车 750/AD 天窗对 A8 03 就是这样）。"""

  def __init__(self, responses: dict[bytes, bytes | list[bytes]] | None = None,
               handler: Callable[[bytes], bytes | list[bytes] | None] | None = None, misnumbered_frames: bool = False):
    self.responses = dict(responses or {})
    self.handler = handler
    self.misnumbered_frames = misnumbered_frames
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
    first_index = 2 if self.ecus[key].misnumbered_frames else 1
    self._pending_tx[key] = [bytes([0x20 | ((i + first_index) & 0x0F)]) + c for i, c in enumerate(chunks)]
    self._emit(key, first)

  def _emit(self, key, frame: bytes):
    tx, sub = key
    data = (bytes([sub]) if sub is not None else b"") + frame
    self._rx.append((tx + 8, data.ljust(8, b"\x00"), self.bus))


class FakeToyotaCanEcu(FakeEcu):
  """有内存的 Toyota CAN 定制 ECU（车窗类）：A803 报掩码，21 读压缩数据，3B 按掩码写回。"""

  def __init__(self, memory: dict[int, bytes], masks: dict[int, bytes], reject_writes: bytes | None = None):
    super().__init__(handler=self._handle)
    self.memory = dict(memory)    # 本地 ID → 压缩数据
    self.masks = dict(masks)      # 本地 ID → A803 掩码
    self.reject_writes = reject_writes

  def _handle(self, request: bytes):
    if request == b"\x3e":
      return b"\x7e"
    if request == b"\xa8\x03":
      return b"\xe8\x03" + b"".join(bytes([i, len(m)]) + m for i, m in self.masks.items())
    if len(request) == 2 and request[0] == 0x21 and request[1] in self.memory:
      return b"\x61" + request[1:2] + self.memory[request[1]]
    if len(request) > 2 and request[0] == 0x3B and request[1] in self.masks:
      if self.reject_writes:
        return self.reject_writes
      self.memory[request[1]] = bytes(b for b, m in zip(request[2:], self.masks[request[1]], strict=True) if m)
      return [b"\x7f\x3b\x78", b"\x7b" + request[1:2]]
    return b"\x7f" + request[:1] + b"\x11"


class FakeUdsEcu(FakeEcu):
  """有内存的 UDS ECU：22 按 DID 读（XX00 位图也是一个 DID）、2E 写进内存、10 03 切扩展会话。

  writes_need_extended_session：默认会话下 2E 回 7F 2E 7F，发过 10 03 才能写。
  ignore_writes：2E 照回肯定应答但内存不变（写入没生效，回读校验应失败）。
  clear_needs_extended_session：默认会话下 14 FFFFFF 回 7F 14 11，发过 10 03 才查 responses 表。
  其余请求（3E、19 02 8D、14 FFFFFF…）查 responses 表，查不到回 7F xx 11。
  """

  def __init__(self, memory: dict[int, bytes], responses: dict[bytes, bytes | list[bytes]] | None = None,
               reject_writes: bytes | None = None, writes_need_extended_session: bool = False, ignore_writes: bool = False,
               clear_needs_extended_session: bool = False):
    super().__init__(responses, handler=self._handle)
    self.memory = dict(memory)
    self.reject_writes = reject_writes
    self.writes_need_extended_session = writes_need_extended_session
    self.ignore_writes = ignore_writes
    self.clear_needs_extended_session = clear_needs_extended_session
    self.extended_session = False

  @classmethod
  def from_probe(cls, path: str, tx: str, sub: str | None, **kwargs) -> "FakeUdsEcu":
    """用实车只读探测记录（tests/fixtures/sienna_ready_probe.jsonl）回放这个 ECU。"""
    memory: dict[int, bytes] = {}
    responses: dict[bytes, bytes] = {}
    with open(path) as f:
      for line in f:
        row = json.loads(line)
        if row.get("tx") != tx or row.get("sub") != sub or "resp" not in row:
          continue
        request, response = bytes.fromhex(row["req"]), bytes.fromhex(row["resp"])
        if request[0] == 0x22 and response[0] == 0x62:
          memory[int.from_bytes(request[1:3], "big")] = response[3:]
        else:
          responses[request] = response
    return cls(memory, responses, **kwargs)

  def _handle(self, request: bytes):
    if request == b"\x10\x03":
      self.extended_session = True
      return b"\x50\x03"
    did = int.from_bytes(request[1:3], "big") if len(request) >= 3 else None
    if request[0] == 0x22 and len(request) == 3:
      return b"\x62" + request[1:3] + self.memory[did] if did in self.memory else b"\x7f\x22\x31"
    if request[0] == 0x2E and did in self.memory:
      if self.reject_writes:
        return self.reject_writes
      if self.writes_need_extended_session and not self.extended_session:
        return b"\x7f\x2e\x7f"
      if not self.ignore_writes:
        self.memory[did] = request[3:]
      return [b"\x7f\x2e\x78", b"\x6e" + request[1:3]]
    if request[0] == 0x14 and self.clear_needs_extended_session and not self.extended_session:
      return b"\x7f\x14\x11"
    return self.responses.get(request, b"\x7f" + request[:1] + b"\x11")
