"""LANLink 丰田诊断的 HTTP 适配层：参数校验 → toyota/* → (status, body)。

方法都是阻塞的（等 pandad 退出、等 ECU 应答），lanlinkd 用 asyncio.to_thread 调用。
body 为 None 表示 204。协议细节见 toyota/ 下各模块和 .scratch/toyota-diag/spec.md。
"""
import subprocess
from collections.abc import Callable

from openpilot.system.lanlinkd.toyota.session import DiagSession, SessionError, car_is_parked

BACKUP_DIR = "/data/lanlink/toyota_backups"
IDLE_CHECK_PERIOD_S = 10


def _process_alive(cmd: list[str]) -> bool:
  try:
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
  except FileNotFoundError:  # 开发机（macOS）没有 pidof
    return False


def pandad_running(run: Callable[[list[str]], bool] = _process_alive) -> bool:
  """原生 pandad 和 Python 包装进程都消失才算停了（同 eps-patch preflight）。"""
  return run(["pidof", "pandad"]) or run(["pgrep", "-f", r"selfdrive\.pandad\.pandad"])


def open_panda_link():
  from opendbc.car.structs import CarParams
  from panda import Panda

  from openpilot.system.lanlinkd.toyota.link import DiagLink
  return DiagLink(Panda(), CarParams.SafetyModel.elm327)


def create(params, status_snapshot: Callable[[], dict]) -> "ToyotaDiagApi":
  session = DiagSession(params, open_link=open_panda_link, pandad_running=pandad_running,
                        is_parked=lambda: car_is_parked(status_snapshot()))
  session.reset_on_startup()
  return ToyotaDiagApi(session, BACKUP_DIR)


def _guarded(operation: Callable[[], dict | None], status: int = 200) -> tuple[int, dict | None]:
  try:
    body = operation()
  except SessionError as exc:
    return exc.status, {"error": exc.message}
  return (204, None) if body is None else (status, body)


class ToyotaDiagApi:
  def __init__(self, session: DiagSession, backup_dir: str):
    self.session = session
    self.backup_dir = backup_dir

  def idle_watchdog(self, exit_event) -> None:
    """后台线程：空闲超时自动退出诊断模式；lanlinkd 退出时也退出，把 pandad 还回去。"""
    while not exit_event.wait(IDLE_CHECK_PERIOD_S):
      self.session.expire_if_idle()
    self.session.exit()

  # ---- 会话 ----
  def session_get(self):
    return 200, self.session.snapshot()

  def session_enter(self):
    def enter():
      self.session.enter()
      return self.session.snapshot()
    return _guarded(enter)

  def session_exit(self):
    return _guarded(self.session.exit)
