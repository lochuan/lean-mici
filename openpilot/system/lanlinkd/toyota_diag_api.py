"""LANLink 丰田诊断的 HTTP 适配层：参数校验 → toyota/* → (status, body)。

方法都是阻塞的（等 pandad 退出、等 ECU 应答），lanlinkd 用 asyncio.to_thread 调用。
body 为 None 表示 204。协议细节见 toyota/ 下各模块和 .scratch/toyota-diag/spec.md。
"""
import subprocess
from collections.abc import Callable

from openpilot.system.lanlinkd.toyota import catalog, customization
from openpilot.system.lanlinkd.toyota.backups import BackupStore
from openpilot.system.lanlinkd.toyota.link import NoResponseError
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


class RequestError(Exception):
  def __init__(self, status: int, message: str):
    super().__init__(message)
    self.status = status
    self.message = message


def _guarded(operation: Callable[[], dict | None], status: int = 200) -> tuple[int, dict | None]:
  try:
    body = operation()
  except (SessionError, RequestError) as exc:
    return exc.status, {"error": exc.message}
  except NoResponseError as exc:
    return 504, {"error": f"ECU 无响应：{exc}"}
  except customization.EcuRejectedError as exc:
    return 502, {"error": str(exc)}
  except customization.StaleDataError as exc:
    return 409, {"error": f"{exc}，请重新预览"}
  return (204, None) if body is None else (status, body)


def _customization_view(item: dict) -> dict:
  return {
    "uid": item["uid"], "group": catalog.group_title(item["group"]), "name": catalog.text(item["name"]),
    "ecu": catalog.ecu_name(item["tx"], item["sub"]),
    "options": [{"index": i, "title": catalog.text(o["title"])} for i, o in enumerate(item["options"])],
    "current": item["current"], "raw": item["raw"], "writable": not item["readOnly"],
  }


def _backup_view(backup: dict) -> dict:
  return {"id": backup["id"], "time": backup["time"], "uid": backup["uid"], "name": catalog.text(backup["name"]),
          "ecu": catalog.ecu_name(backup["tx"], backup["sub"]), "localId": backup["writeId"], "data": backup["data"]}


class ToyotaDiagApi:
  def __init__(self, session: DiagSession, backup_dir: str,
               customization_items: Callable[[], list[dict]] = catalog.customization_items):
    self.session = session
    self.backups = BackupStore(backup_dir)
    self._customization_items = customization_items

  def _item_and_option(self, uid: str, body: dict, writing: bool = True) -> tuple[dict, int]:
    item = next((i for i in self._customization_items() if str(i["uid"]) == uid), None)
    if item is None:
      raise RequestError(404, "未知定制项")
    if writing and item["readOnly"]:
      raise RequestError(409, "该项读写 ID 不一致，已禁止写入")
    option = body.get("option")
    if not isinstance(option, int) or isinstance(option, bool) or not 0 <= option < len(item["options"]):
      raise RequestError(400, "option 无效")
    return item, option

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

  # ---- 定制 ----
  def customizations_scan(self):
    return _guarded(lambda: {"items": [_customization_view(i) for i in
                                       self.session.run(lambda link: customization.scan(link, self._customization_items()))]})

  def customization_preview(self, uid: str, body: dict):
    def preview():
      item, option = self._item_and_option(uid, body)
      return {"uid": item["uid"], **self.session.run(lambda link: customization.preview(link, item, option))}
    return _guarded(preview)

  def customization_write(self, uid: str, body: dict):
    def write():
      item, option = self._item_and_option(uid, body)
      before = body.get("before")
      if not isinstance(before, str):
        raise RequestError(400, "缺少 before（预览时的原始数据）")
      return {"uid": item["uid"], **self.session.run(lambda link: customization.write(link, item, option, before, self.backups))}
    return _guarded(write)

  # ---- 备份 ----
  def backups_list(self):
    return 200, {"backups": [_backup_view(b) for b in self.backups.list()]}

  def backup_restore(self, backup_id: str):
    def restore():
      backup = self.backups.get(backup_id)
      if backup is None:
        raise RequestError(404, "备份不存在")
      return self.session.run(lambda link: customization.restore(link, backup))
    return _guarded(restore)
