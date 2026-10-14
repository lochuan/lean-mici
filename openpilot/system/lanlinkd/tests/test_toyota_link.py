import pytest

from openpilot.system.lanlinkd.toyota import link as link_mod
from openpilot.system.lanlinkd.toyota.link import DiagLink, MalformedResponseError, NoResponseError

from .toyota_fakes import FakeEcu, FakePanda

ELM327 = 3  # 只用于断言透传


class OneFramePerPollPanda(FakePanda):
  """每次 can_recv 只交出一帧：模拟 ECU 按 STmin 逐帧发续帧。"""

  def can_recv(self):
    return [self._rx.pop(0)] if self._rx else []


def make_link(ecus, **kwargs):
  panda = FakePanda(ecus)
  kwargs.setdefault("timeout", 0.05)
  return panda, DiagLink(panda, elm327_mode=ELM327, heartbeat_thread=False, **kwargs)


class TestOpen:
  def test_sets_elm327_without_obd_multiplexing(self):
    panda, _ = make_link({})
    # 参数 1 = 不把 bus 1 切到 OBD 口：我们走的是摄像头线束的 bus 0
    assert panda.safety_modes == [(ELM327, 1)]

  def test_close_releases_panda(self):
    panda, link = make_link({})
    link.close()
    assert panda.closed


class TestRequest:
  def test_single_frame_direct_ecu(self):
    _, link = make_link({(0x7C0, None): FakeEcu({b"\x21\xa7": b"\x61\xa7\x12\x34"})})
    assert link.request(0x7C0, None, b"\x21\xa7") == b"\x61\xa7\x12\x34"

  def test_gateway_sub_address_multi_frame_both_ways(self):
    # 10 字节写请求要分段发；12 字节响应要分段收，且每帧首字节都是子地址 0x40
    request = bytes.fromhex("3b11") + bytes(range(8))
    response = bytes.fromhex("6111") + bytes(range(10))
    ecu = FakeEcu({request: response})
    panda, link = make_link({(0x750, 0x40): ecu})
    assert link.request(0x750, 0x40, request) == response
    assert ecu.requests == [request]
    assert all(dat[0] == 0x40 for addr, dat, _ in panda.sent if addr == 0x750)

  def test_waits_through_response_pending(self):
    ecu = FakeEcu({b"\x3b\x12\x01": [b"\x7f\x3b\x78", b"\x7b\x12"]})
    _, link = make_link({(0x750, 0x40): ecu})
    assert link.request(0x750, 0x40, b"\x3b\x12\x01") == b"\x7b\x12"

  def test_negative_response_is_returned_not_raised(self):
    # 读码/清码要按 NRC 换下一条命令，所以负响应原样交给调用方判断
    _, link = make_link({(0x7E0, None): FakeEcu({b"\x13": b"\x7f\x13\x11"})})
    assert link.request(0x7E0, None, b"\x13") == b"\x7f\x13\x11"

  def test_silent_ecu_raises_no_response(self):
    _, link = make_link({})
    with pytest.raises(NoResponseError):
      link.request(0x7E0, None, b"\x3e")

  def test_misnumbered_consecutive_frames_raise_malformed_response(self):
    # 实车 750/AD 对 A8 03 的多帧应答序号错乱，opendbc 会抛 AssertionError；要变成能按 ECU 处理的应答异常
    ecu = FakeEcu({b"\xa8\x03": b"\xe8\x03" + bytes(range(20))}, misnumbered_frames=True)
    _, link = make_link({(0x750, 0xAD): ecu})
    with pytest.raises(MalformedResponseError):
      link.request(0x750, 0xAD, b"\xa8\x03")

  @pytest.mark.parametrize("next_address, next_request, next_response", [
    ((0x750, 0x40), b"\x21\x12", b"\x61\x12\xa0"),   # 网关后的另一个 ECU：残留帧子地址不同
    ((0x750, 0xAD), b"\x19\x02\x8d", b"\x7f\x19\x11"),  # 同一个 ECU：残留续帧不能拼进新应答
  ])
  def test_late_frames_of_malformed_response_do_not_break_next_request(self, next_address, next_request, next_response):
    # 实车 ECU 在序号错乱后还会按 STmin 把剩下的续帧发完，下一次请求时它们才陆续到达
    sunroof = FakeEcu({b"\xa8\x03": b"\xe8\x03" + bytes(range(60)), b"\x19\x02\x8d": b"\x7f\x19\x11"}, misnumbered_frames=True)
    body = FakeEcu({b"\x21\x12": b"\x61\x12\xa0"})
    panda = OneFramePerPollPanda({(0x750, 0xAD): sunroof, (0x750, 0x40): body})
    link = DiagLink(panda, elm327_mode=ELM327, heartbeat_thread=False, timeout=0.05)
    with pytest.raises(MalformedResponseError):
      link.request(0x750, 0xAD, b"\xa8\x03")
    assert link.request(*next_address, next_request) == next_response

  def test_slow_multi_frame_response_is_not_a_timeout(self, monkeypatch):
    # A8 03 的掩码表可达几十帧；ECU 逐帧慢慢发时，总耗时超过单次超时也不能当成无响应
    response = b"\xe8\x03" + bytes(range(60))
    panda = OneFramePerPollPanda({(0x7C0, None): FakeEcu({b"\xa8\x03": response})})
    link = DiagLink(panda, elm327_mode=ELM327, heartbeat_thread=False, timeout=0.05)
    clock = iter(range(100000))
    monkeypatch.setattr(link_mod.time, "monotonic", lambda: next(clock) * 0.02)
    assert link.request(0x7C0, None, b"\xa8\x03") == response

  def test_heartbeat_sent_while_waiting(self, monkeypatch):
    # 熄火时 panda 2 s 收不到心跳就进 SILENT；长等待期间也要续心跳
    ecu = FakeEcu({b"\x3b\x12\x01": [b"\x7f\x3b\x78"]})
    panda = FakePanda({(0x750, 0x40): ecu})
    link = DiagLink(panda, elm327_mode=ELM327, heartbeat_thread=False, timeout=0.05, pending_timeout=0.2)
    clock = iter(range(1000))
    monkeypatch.setattr(link_mod.time, "monotonic", lambda: next(clock) * 0.01)
    with pytest.raises(NoResponseError):
      link.request(0x750, 0x40, b"\x3b\x12\x01")
    assert panda.heartbeats > 0
