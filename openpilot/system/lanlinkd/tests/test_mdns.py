"""_lanlink._tcp 发布的命令契约（avahi-publish-service 参数）。

子进程随服务启停在设备上验证（票 05）；这里钉死「发布出去的是什么」：
服务类型、端口、TXT 里的版本和设备名——App 端（NsdManager）靠它发现 C4。
"""
from openpilot.system.lanlinkd import mdns


def test_publish_argv_carries_version_and_device_name():
  argv = mdns.publish_argv(name="comma-55873f9", version="0.9.8", device="comma-55873f9")
  assert argv == ["avahi-publish-service", "comma-55873f9", "_lanlink._tcp", "8088",
                  "version=0.9.8", "device=comma-55873f9"]


def test_missing_binary_degrades_to_no_publication(monkeypatch):
  # 没有 avahi 的开发机/CI 上服务照常跑，只是不能自动发现，不能直接崩
  def no_such_bin(*a, **k):
    raise FileNotFoundError("avahi-publish-service")
  monkeypatch.setattr(mdns.subprocess, "Popen", no_such_bin)
  assert mdns.start("x", "1", "mici") is None


def test_stop_terminates_the_publisher():
  class FakeProc:
    def __init__(self):
      self.terminated = False

    def poll(self):
      return None

    def terminate(self):
      self.terminated = True

    def wait(self, timeout=None):
      return 0

  proc = FakeProc()
  mdns.stop(proc)
  assert proc.terminated, "退出时必须结束发布子进程"
  mdns.stop(None)  # 没拉起来过也不报错
