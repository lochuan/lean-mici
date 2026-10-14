#!/usr/bin/env python3
"""LANLink 丰田诊断的回放预览服务：不连车也能在浏览器里走通丰田页（只供开发，不随设备打包）。

用实车 Ready 只读探测记录（openpilot/system/lanlinkd/tests/fixtures/sienna_ready_probe.jsonl）拼一台假
Sienna：UDS ECU 用 FakeUdsEcu 回放（写入落在内存里，清码后故障码清空）；Toyota CAN 车窗类 ECU 用探测到的
A8 03 掩码表、数据全 0（探测时没发过 21）。挂上真实的 ToyotaDiagApi 提供 /api/toyota/*，
会话用内存状态代替（不停 pandad、不碰 panda）。备份写在临时目录，进程退出即丢。

用法（仓库根目录）：
  PYTHONPATH=$PWD:$PWD/opendbc_repo python3 tools/lanlink/toyota_replay_server.py [--port 8099]
  cd openpilot/system/lanlinkd/web && LANLINK_TOYOTA_REPLAY=http://127.0.0.1:8099 npx vite
vite dev 把 /api/toyota 代理到这里，其余 /api 照旧代理到设备（LANLINK_DEVICE）。
"""
import argparse
import json
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openpilot.system.lanlinkd.tests.toyota_fakes import FakeToyotaCanEcu, FakePanda, FakeUdsEcu
from openpilot.system.lanlinkd.toyota import catalog, coding
from openpilot.system.lanlinkd.toyota.link import DiagLink
from openpilot.system.lanlinkd.toyota.session import SessionError
from openpilot.system.lanlinkd.toyota_diag_api import ToyotaDiagApi

PROBE = "openpilot/system/lanlinkd/tests/fixtures/sienna_ready_probe.jsonl"
IDLE_TIMEOUT_S = 900
REQUEST_LATENCY_S = 0.08   # 每次调用的人为延迟：让逐 ECU 扫描的进度条看得见
UDS_READ_DTC = bytes.fromhex("19028d")
UDS_CLEAR_DTC = bytes.fromhex("14ffffff")

# (方法, 路径正则, ToyotaDiagApi 方法, 是否带请求体)；与 lanlinkd.py 的 /api/toyota 路由一致
ROUTES = [
  ("GET", r"/api/toyota/session", "session_get", False),
  ("GET", r"/api/toyota/catalog", "catalog_get", False),
  ("POST", r"/api/toyota/session/enter", "session_enter", False),
  ("POST", r"/api/toyota/session/exit", "session_exit", False),
  ("POST", r"/api/toyota/customizations/scan", "customizations_scan", True),
  ("POST", r"/api/toyota/customizations/([^/]+)/write", "customization_write", True),
  ("GET", r"/api/toyota/backups", "backups_list", False),
  ("POST", r"/api/toyota/backups/([^/]+)/restore", "backup_restore", False),
  ("POST", r"/api/toyota/dtc/scan", "dtc_scan", True),
  ("POST", r"/api/toyota/dtc/clear", "dtc_clear", True),
  ("POST", r"/api/toyota/live/read", "live_read", True),
]


class ReplayUdsEcu(FakeUdsEcu):
  """清码后 19 02 8D 只剩可用掩码，页面上能看到「已清除」的效果。"""

  def _handle(self, request: bytes):
    if request == UDS_CLEAR_DTC:
      self.responses[UDS_READ_DTC] = self.responses.get(UDS_READ_DTC, b"\x59\x02\xff")[:3]
      return b"\x54"
    return super()._handle(request)


def _probed_can_mask_table(tx: str, sub: str | None) -> bytes:
  with open(PROBE) as f:
    for line in f:
      row = json.loads(line)
      if row.get("tx") == tx and row.get("sub") == sub and row.get("req") == "a803" and "resp" in row:
        return bytes.fromhex(row["resp"])
  return b""


def build_car() -> FakePanda:
  ecus = {}
  for ecu in catalog.load_sienna()["ecus"]:
    if ecu["protocol"] == "toyota_can":
      masks = coding.parse_masks(_probed_can_mask_table(ecu["tx"], ecu["sub"]))
      fake = FakeToyotaCanEcu({i: bytes(sum(1 for b in m if b)) for i, m in masks.items()}, masks)
    else:
      fake = ReplayUdsEcu.from_probe(PROBE, ecu["tx"], ecu["sub"])
    ecus[catalog.ecu_address(ecu)] = fake
  return FakePanda(ecus)


class ReplaySession:
  """DiagSession 的内存替身：进入/退出只改状态，run 持锁调用回放链路。"""

  def __init__(self, link: DiagLink):
    self._link = link
    self._lock = threading.Lock()
    self._active = False
    self._last_activity = 0.0

  def snapshot(self) -> dict:
    if not self._active:
      return {"state": "idle", "idleTimeoutS": IDLE_TIMEOUT_S}
    remaining = max(0, round(IDLE_TIMEOUT_S - (time.monotonic() - self._last_activity)))
    return {"state": "active", "idleTimeoutS": IDLE_TIMEOUT_S, "lastActivity": self._last_activity, "idleRemainingS": remaining}

  def enter(self) -> None:
    self._active, self._last_activity = True, time.monotonic()

  def exit(self) -> None:
    self._active = False

  def expire_if_idle(self) -> None:
    if self._active and time.monotonic() - self._last_activity > IDLE_TIMEOUT_S:
      self._active = False

  def run(self, operation):
    with self._lock:
      if not self._active:
        raise SessionError(409, "请先进入诊断模式")
      time.sleep(REQUEST_LATENCY_S)
      self._last_activity = time.monotonic()
      return operation(self._link)


def make_handler(api: ToyotaDiagApi):
  class Handler(BaseHTTPRequestHandler):
    def _dispatch(self, method: str) -> None:
      path = self.path.split("?", 1)[0]
      for route_method, pattern, name, takes_body in ROUTES:
        match = re.fullmatch(pattern, path)
        if route_method == method and match:
          args = list(match.groups())
          if takes_body:
            length = int(self.headers.get("Content-Length") or 0)
            args.append(json.loads(self.rfile.read(length) or b"{}"))
          api.session.expire_if_idle()
          status, body = getattr(api, name)(*args)
          return self._reply(status, body)
      self._reply(404, {"error": "回放服务没有这个接口"})

    def _reply(self, status: int, body: dict | None) -> None:
      data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode()
      self.send_response(status)
      if body is not None:
        self.send_header("Content-Type", "application/json; charset=utf-8")
      self.send_header("Content-Length", str(len(data)))
      self.end_headers()
      self.wfile.write(data)

    def do_GET(self):
      self._dispatch("GET")

    def do_POST(self):
      self._dispatch("POST")

  return Handler


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
  parser.add_argument("--host", default="127.0.0.1")
  parser.add_argument("--port", type=int, default=8099)
  args = parser.parse_args()
  link = DiagLink(build_car(), elm327_mode=3, heartbeat_thread=False, timeout=0.05, pending_timeout=0.2)
  backup_dir = tempfile.mkdtemp(prefix="toyota-replay-backups-")
  api = ToyotaDiagApi(ReplaySession(link), backup_dir)
  print(f"丰田诊断回放预览：http://{args.host}:{args.port}/api/toyota/ （备份目录 {backup_dir}）", flush=True)
  ThreadingHTTPServer((args.host, args.port), make_handler(api)).serve_forever()


if __name__ == "__main__":
  main()
