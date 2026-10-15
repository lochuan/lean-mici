# system/lanlinkd/mdns.py
"""_lanlink._tcp 发布：``avahi-publish-service`` 子进程，跟随 lanlinkd 启停。

App（Chipmunk）用 NsdManager 发现本服务（票 06），手动 IP 兜底。实例名带设备名，
TXT 带版本和设备名；lanlinkd 退出（含 manager 因 LanLinkEnabled 关掉拉闸）时
子进程一起结束，广播随之消失——App 不会连到一个不存在的服务。

发布工具用 avahi-publish-service（设备自带 avahi 客户端工具，bigmodeld 同机
在用 avahi-browse）。它缺位时降级为不发布：服务照常跑，只是不能自动发现。
"""
import signal
import subprocess

from openpilot.common.swaglog import cloudlog

SERVICE_TYPE = "_lanlink._tcp"
SERVICE_PORT = 8088
PUBLISH_BIN = "avahi-publish-service"

# PR_SET_PDEATHSIG：内核在父进程死亡时给子进程发信号
PR_SET_PDEATHSIG = 1


def publish_argv(name: str, version: str, device: str) -> list[str]:
  """avahi-publish-service 的参数契约：类型 _lanlink._tcp，端口 8088，TXT 带版本和设备名。"""
  return [PUBLISH_BIN, name, SERVICE_TYPE, str(SERVICE_PORT), f"version={version or 'unknown'}", f"device={device}"]


def _die_with_parent() -> None:  # pragma: no cover  （只在 fork 出的子进程里跑）
  try:
    import ctypes
    ctypes.CDLL("libc.so.6").prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
  except Exception:
    pass  # 非 Linux（开发机）无 libc.so.6：退化为随正常退出结束


def start(name: str, version: str, device: str) -> subprocess.Popen | None:
  """拉起发布子进程；工具缺位返回 None（服务照常，只是不能自动发现）。"""
  try:
    proc = subprocess.Popen(publish_argv(name, version, device), preexec_fn=_die_with_parent)
  except FileNotFoundError:
    cloudlog.warning("lanlinkd: avahi-publish-service not found, mDNS discovery unavailable")
    return None
  cloudlog.info(f"lanlinkd: publishing {SERVICE_TYPE} as {name!r} (version={version or 'unknown'})")
  return proc


def stop(proc: subprocess.Popen | None) -> None:
  if proc is None or proc.poll() is not None:
    return
  proc.terminate()
  try:
    proc.wait(timeout=5)
  except subprocess.TimeoutExpired:
    proc.kill()
