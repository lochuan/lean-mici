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

from opendbc.car.uds import CanClient, IsoTpMessage

HEARTBEAT_PERIOD_S = 0.5
RESPONSE_PENDING = 0x78
NEGATIVE_RESPONSE = 0x7F


class NoResponseError(Exception):
  """ECU 在超时内没有应答（不存在、未上电或不支持该地址）。"""


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
    client = CanClient(self.panda.can_send, self.panda.can_recv, tx, tx + 8, self.bus, sub)
    message = IsoTpMessage(client, timeout=self.timeout)
    message.send(payload)
    deadline = time.monotonic() + self.timeout
    while True:
      response, _ = message.recv(timeout=0)
      now = time.monotonic()
      if response is None:
        if now > deadline:
          raise NoResponseError(f"{tx:X}/{sub if sub is None else f'{sub:02X}'} {payload.hex()}: no response")
        if now - self._last_heartbeat >= HEARTBEAT_PERIOD_S:
          self._heartbeat()
        time.sleep(0.001)
        continue
      if len(response) >= 3 and response[0] == NEGATIVE_RESPONSE and response[1] == payload[0] and response[2] == RESPONSE_PENDING:
        deadline = now + self.pending_timeout
        continue
      return response

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
