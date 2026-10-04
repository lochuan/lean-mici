#!/usr/bin/env python3
"""台架 jungle 回放：把 Sienna 的 CAN 循环灌给设备并点火，让设备进 onroad。

jungle 是 V1(F4)，当前 panda pin 不支持，且固件 e462c34d 不得刷写、不得绕过硬件检查：
这里从 panda 子模块导出同版本 (e462c34d) 的旧库到缓存目录只读使用。
用法:
  jungle_replay.py          点火并循环回放（前台运行，Ctrl-C 或 kill 即停）
  jungle_replay.py --off    熄火并退出
"""
import ctypes
import io
import lzma
import pickle
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAMES = Path(__file__).with_name("sienna_can_loop.xz")  # 取自 Sienna route 00000052--696b66504b--17，约 60 秒，48~64 km/h
OLD_PANDA_REV = "e462c34d"
CACHE = Path.home() / ".cache" / "jungle_v1_panda"


def load_old_panda() -> None:
  if not (CACHE / "panda").exists():
    CACHE.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(["git", "-C", str(ROOT / "panda"), "archive", OLD_PANDA_REV], check=True, capture_output=True).stdout
    (CACHE / "panda").mkdir()
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
      t.extractall(CACHE / "panda")
  sys.path.insert(0, str(CACHE))


def main() -> None:
  load_old_panda()
  import usb1
  import libusb_package
  usb1._libusb1.loadLibrary(ctypes.CDLL(str(libusb_package.get_library_path())))
  from panda import PandaJungle

  jungles = PandaJungle.list()
  if not jungles:
    sys.exit("没找到 jungle")
  jungle = PandaJungle(jungles[0])

  if "--off" in sys.argv:
    jungle.set_ignition(False)
    print("ignition off")
    return

  frames = pickle.load(lzma.open(FRAMES))
  for bus in [0, 1, 2, 3, 0xFFFF]:
    jungle.can_clear(bus)
  for bus in [0, 1, 2]:
    jungle.set_can_speed_kbps(bus, 500)
  jungle.set_ignition(True)
  jungle.set_panda_power(True)
  jungle.set_can_loopback(False)
  print("replaying", len(frames), "frames @100Hz", flush=True)
  t0, i = time.monotonic(), 0
  while True:
    try:
      jungle.can_send_many(frames[i % len(frames)])
    except usb1.USBErrorTimeout:
      pass
    jungle.can_recv()
    i += 1
    time.sleep(max(0.0, t0 + i * 0.01 - time.monotonic()))


if __name__ == "__main__":
  main()
