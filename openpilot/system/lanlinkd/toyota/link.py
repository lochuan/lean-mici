"""诊断模式下独占 panda 的 ISO-TP 链路。

只在 pandad 已停时使用（见 session.py）：panda 切 ELM327 safety（只放行
0x6xx/0x7xx 诊断帧），bus 0 即摄像头线束上的整车 CAN，0x750 网关用
Toyota 扩展地址（每帧首字节 = 子地址）。

panda 超过 2 s（熄火）/ 5 s（点火）收不到心跳就进 SILENT，所以后台线程
定时续心跳；单次请求等待 ECU 时（NRC 0x78 可能拖好几秒）也顺手续一次。
SPI 句柄自带锁，心跳线程与请求线程并发调用是安全的。
"""
import threading
import time

from opendbc.car.uds import CanClient, InvalidSubAddressError, IsoTpMessage

HEARTBEAT_PERIOD_S = 0.5
RESPONSE_PENDING = 0x78
NEGATIVE_RESPONSE = 0x7F
EXTENDED_SESSION = b"\x10\x03"


class NoResponseError(Exception):
  """ECU 在超时内没有应答（不存在、未上电或不支持该地址）。"""


class MalformedResponseError(Exception):
  """ECU 有应答但不符合 ISO-TP（如续帧序号错乱，实车 750/AD 天窗的 A8 03 应答必现）。"""


class EcuRejectedError(Exception):
  """ECU 回了负响应，或正响应的回显不对。"""

  def __init__(self, request: bytes, response: bytes):
    self.nrc = response[2] if len(response) >= 3 and response[0] == NEGATIVE_RESPONSE else None
    super().__init__(f"ECU 拒绝 {request[:2].hex()}：{response.hex()}")


# 与 ECU 通信失败的三种形态：按 ECU / 按数据项记下就继续（扫描、清码都不能因为一个 ECU 整体失败）
ECU_COMMUNICATION_ERRORS = (NoResponseError, MalformedResponseError, EcuRejectedError)


def read_error_text(exc: Exception) -> str:
  """ECU_COMMUNICATION_ERRORS 里的错误给用户看的简短原因。"""
  if isinstance(exc, NoResponseError):
    return "无响应"
  if isinstance(exc, MalformedResponseError):
    return "应答异常"
  return f"NRC {exc.nrc:02X}" if getattr(exc, "nrc", None) is not None else "应答格式不符"


def expect_positive(link, address: tuple[int, int | None], request: bytes, echo_len: int) -> bytes:
  """发请求，要求正响应（SID+0x40 且回显请求的前 echo_len 字节），返回回显之后的数据。"""
  response = link.request(*address, request)
  if response[:echo_len] != bytes([request[0] + 0x40]) + request[1:echo_len]:
    raise EcuRejectedError(request, response)
  return response[echo_len:]


def _request_label(tx: int, sub: int | None, payload: bytes) -> str:
  return f"{tx:X}/{sub if sub is None else f'{sub:02X}'} {payload.hex()}"


class DiagLink:
  def __init__(self, panda, elm327_mode, bus: int = 0, timeout: float = 0.3, pending_timeout: float = 5.0,
               heartbeat_thread: bool = True):
    self.panda = panda
    self.bus = bus
    self.timeout = timeout
    self.pending_timeout = pending_timeout
    self._last_heartbeat = 0.0
    self._closed = threading.Event()
    # 参数 1：不把 bus 1 复用到 OBD 口，我们只用 bus 0
    panda.set_safety_mode(elm327_mode, 1)
    self._heartbeat()
    if heartbeat_thread:
      threading.Thread(target=self._heartbeat_loop, name="toyota_diag_heartbeat", daemon=True).start()

  def request(self, tx: int, sub: int | None, payload: bytes) -> bytes:
    """发一条诊断请求，返回 ECU 的最终响应（正响应或 7F 负响应，原样返回）。"""
    client = CanClient(self.panda.can_send, self._receiver_for(tx + 8, sub), tx, tx + 8, self.bus, sub)
    message = IsoTpMessage(client, timeout=self.timeout)
    message.send(payload)
    deadline = time.monotonic() + self.timeout
    while True:
      try:
        response, frame_received = message.recv(timeout=0)
      except (AssertionError, InvalidSubAddressError) as exc:  # opendbc 用 assert 校验 ISO-TP 帧
        self._discard_until_quiet(tx + 8)
        raise MalformedResponseError(f"{_request_label(tx, sub, payload)}: {exc}") from exc
      now = time.monotonic()
      if response is None:
        if frame_received:
          # 多帧响应接收中（A8 03 的掩码表可达几十帧）：每收到一帧就续期，只在 ECU 停发后才算超时
          deadline = now + self.timeout
        elif now > deadline:
          raise NoResponseError(f"{_request_label(tx, sub, payload)}: no response")
        if now - self._last_heartbeat >= HEARTBEAT_PERIOD_S:
          self._heartbeat()
        time.sleep(0.001)
        continue
      if len(response) >= 3 and response[0] == NEGATIVE_RESPONSE and response[1] == payload[0] and response[2] == RESPONSE_PENDING:
        deadline = now + self.pending_timeout
        continue
      return response

  def _receiver_for(self, rx_addr: int, sub: int | None):
    """网关请求只收本子地址的帧：别的子 ECU 迟到的帧（如上一个应答异常的残留续帧）直接丢掉，不算应答异常。"""
    if sub is None:
      return self.panda.can_recv
    return lambda: [f for f in self.panda.can_recv() if f[0] != rx_addr or (len(f[1]) > 1 and f[1][0] == sub)]

  def _discard_until_quiet(self, rx_addr: int) -> None:
    """应答异常时 ECU 往往还在按 STmin 发剩下的续帧：丢到它静默 timeout 为止，免得拼进下一次请求的应答。"""
    now = time.monotonic()
    quiet_at, give_up_at = now + self.timeout, now + self.pending_timeout
    while now < min(quiet_at, give_up_at):
      if any(f[0] == rx_addr for f in self.panda.can_recv()):
        quiet_at = now + self.timeout
      time.sleep(0.001)
      now = time.monotonic()

  def close(self) -> None:
    self._closed.set()
    self.panda.close()

  def _heartbeat(self) -> None:
    self._last_heartbeat = time.monotonic()
    self.panda.send_heartbeat(engaged=False)

  def _heartbeat_loop(self) -> None:
    while not self._closed.wait(HEARTBEAT_PERIOD_S):
      try:
        self._heartbeat()
      except Exception:
        return  # panda 已关闭/断开：由 session 负责收尾
