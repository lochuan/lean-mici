"""pandad.py 包装进程主循环的纯逻辑测试（不碰硬件、不起 ./pandad）。

- 复位后 panda 约 0.2 s 才能枚举到：立刻枚举为空不能当成缺失，否则下一轮 recover 进 DFU、每次启动都重刷固件；
- 刷写期间收到 SIGINT 不能再启动 ./pandad：manager 5 s 后 SIGKILL 包装进程，./pandad 会成为孤儿一直占着 panda。
"""
import os
import signal

import pytest

from panda import Panda
from openpilot.selfdrive.pandad import pandad as pandad_mod

SERIAL = "040016001451323234343035"


class FakeHardware:
  def __init__(self):
    self.calls: list[str] = []
    self.polls_since_reset = 0

  def reset_internal_panda(self):
    self.calls.append("reset")
    self.polls_since_reset = 0

  def recover_internal_panda(self):
    # 走到这里就是回归（旧代码在这里进 DFU 重刷）：结束主循环让断言报错，而不是无限转圈
    self.calls.append("recover")
    Loop.send_sigint_to_self()


class FakeProcess:
  """./pandad 的替身：收到 SIGINT 才退出；wait() 时可以触发外部事件。"""

  def __init__(self, on_wait=None):
    self.signals: list[int] = []
    self.on_wait = on_wait

  def send_signal(self, sig):
    self.signals.append(sig)

  def wait(self):
    if self.on_wait:
      self.on_wait()
    assert signal.SIGINT in self.signals, "./pandad 没收到 SIGINT 就不会退出"


class Loop:
  """把 main() 的所有外部依赖换成假件；panda 在复位后第 boot_polls 次枚举才出现。"""

  def __init__(self, monkeypatch, boot_polls=3):
    self.hardware = FakeHardware()
    self.processes: list[FakeProcess] = []
    self.flashed: list[str] = []
    hardware = self.hardware

    class SlowBootPanda(Panda):
      @classmethod
      def list(cls, usb_only=False):
        hardware.polls_since_reset += 1
        return [SERIAL] if "reset" in hardware.calls and hardware.polls_since_reset >= boot_polls else []

    monkeypatch.setattr(pandad_mod, "HARDWARE", self.hardware)
    monkeypatch.setattr(pandad_mod, "Panda", SlowBootPanda)
    monkeypatch.setattr(pandad_mod.PandaDFU, "list", staticmethod(lambda: []))
    monkeypatch.setattr(pandad_mod, "flash_rivian_long", lambda serials: None)
    monkeypatch.setattr(pandad_mod, "check_panda_support", lambda serials: serials)
    monkeypatch.setattr(pandad_mod, "flash_panda", self.flash_panda)
    monkeypatch.setattr(pandad_mod.subprocess, "Popen", self.popen)
    self.on_flash = None
    self.on_wait = self.send_sigint_to_self

  def flash_panda(self, serial):
    self.flashed.append(serial)
    if self.on_flash:
      self.on_flash()

  def popen(self, *args, **kwargs):
    self.processes.append(FakeProcess(self.on_wait))
    return self.processes[-1]

  @staticmethod
  def send_sigint_to_self():
    os.kill(os.getpid(), signal.SIGINT)  # 相当于 manager 的 stop：信号处理器同步执行


@pytest.fixture(autouse=True)
def restore_sigint_handler():
  previous = signal.getsignal(signal.SIGINT)
  yield
  signal.signal(signal.SIGINT, previous)


def test_waits_for_panda_to_boot_after_reset_instead_of_recovering(monkeypatch):
  loop = Loop(monkeypatch, boot_polls=3)
  pandad_mod.main()
  assert loop.hardware.calls == ["reset"]  # 没有走 recover（DFU 重刷）
  assert loop.flashed == [SERIAL] and len(loop.processes) == 1


def test_sigint_during_flash_does_not_start_pandad(monkeypatch):
  loop = Loop(monkeypatch, boot_polls=1)
  loop.on_flash = Loop.send_sigint_to_self
  pandad_mod.main()
  assert loop.flashed == [SERIAL]
  assert loop.processes == []  # 不启动就不会留下孤儿


def test_sigint_while_pandad_runs_is_forwarded_and_loop_ends(monkeypatch):
  loop = Loop(monkeypatch, boot_polls=1)
  pandad_mod.main()
  assert len(loop.processes) == 1 and loop.processes[0].signals == [signal.SIGINT]
  assert loop.hardware.calls == ["reset"]
