"""LANLink 丰田诊断的 HTTP 适配层：参数校验 → toyota/* → (status, body)。

方法都是阻塞的（等 pandad 退出、等 ECU 应答），lanlinkd 用 asyncio.to_thread 调用。
body 为 None 表示 204。协议细节见 toyota/ 下各模块的文档字符串；前端契约类型见 web/src/lib/schema.ts。
"""
import subprocess
from collections.abc import Callable

from openpilot.common.swaglog import cloudlog
from openpilot.system.lanlinkd import vehicle_api
from openpilot.system.lanlinkd.toyota import catalog, customization, dtc, live_data
from openpilot.system.lanlinkd.toyota.backups import BackupStore
from openpilot.system.lanlinkd.toyota.link import MalformedResponseError, NoResponseError
from openpilot.system.lanlinkd.toyota.session import DiagSession, SessionError, car_is_parked

BACKUP_DIR = "/data/lanlink/toyota_backups"
IDLE_CHECK_PERIOD_S = 10
PID_READ_LIMIT = 40
COMPUTED_GROUP = "计算值"


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
  panda = Panda()
  try:
    return DiagLink(panda, CarParams.SafetyModel.elm327)
  except Exception:
    panda.close()  # 不关的话句柄占着 USB/SPI，pandad 恢复后连不上
    raise


def create(params, status_snapshot: Callable[[], dict]) -> "ToyotaDiagApi":
  session = DiagSession(params, open_link=open_panda_link, pandad_running=pandad_running,
                        is_parked=lambda: car_is_parked(status_snapshot()))
  session.reset_on_startup()
  return ToyotaDiagApi(session, BACKUP_DIR, vin=lambda: vehicle_api.car_vin(params))


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
  except MalformedResponseError as exc:
    return 502, {"error": f"ECU 应答异常：{exc}"}
  except customization.BackupError as exc:
    return 500, {"error": str(exc)}
  except Exception as exc:  # 未预料的错误也给前端 JSON，并留日志（否则 Sanic 只回裸 500）
    cloudlog.exception("lanlink toyota: 未处理的异常")
    return 500, {"error": f"内部错误：{exc}"}
  return (204, None) if body is None else (status, body)


def _customization_view(item: dict) -> dict:
  return {
    "uid": item["uid"], "group": catalog.group_title(item["group"]), "name": catalog.text(item["name"]),
    "ecu": catalog.ecu_name(item["tx"], item["sub"]),
    "options": [{"index": i, "title": catalog.text(o["title"])} for i, o in enumerate(item["options"])],
    "current": item["current"], "raw": item["raw"], "writable": not item["readOnly"],
  }


def _scan_ecu_view(report: dict) -> dict:
  view = {"ecu": catalog.ecu_name(report["tx"], report["sub"]), "status": report["status"],
          "total": report["total"], "supported": report["supported"], "read": report["read"]}
  view.update({k: report[k] for k in ("nrc", "response", "readError", "malformedReason") if k in report})
  return view


def _backup_view(backup: dict) -> dict:
  return {"id": backup["id"], "time": backup["time"], "uid": backup["uid"], "name": catalog.text(backup["name"]),
          "ecu": catalog.ecu_name(backup["tx"], backup["sub"]), "localId": backup["writeId"], "data": backup["data"]}


def _pid_group(item: dict) -> str:
  return catalog.ecu_name(item["hdr"].upper(), item["sub"]) if item["cmd"] else COMPUTED_GROUP


class ToyotaDiagApi:
  def __init__(self, session: DiagSession, backup_dir: str,
               customization_items: Callable[[], list[dict]] = catalog.customization_items,
               dtc_targets: Callable[[], list[dict]] = catalog.dtc_targets,
               pid_profiles: Callable[[], list[dict]] = catalog.pid_profiles,
               vin: Callable[[], str] = lambda: "",
               default_profile_id: Callable[[str], str] = live_data.default_profile_id):
    self.session = session
    self.backups = BackupStore(backup_dir)
    self._customization_items = customization_items
    self._dtc_targets = dtc_targets
    self._pid_profiles = pid_profiles
    self._vin = vin
    self._default_profile_id = default_profile_id
    self._pid_profile_id: str | None = None  # 用户手动选的配置；None = 按 VIN 年份

  def _item_and_option(self, uid: str, body: dict) -> tuple[dict, int]:
    item = next((i for i in self._customization_items() if str(i["uid"]) == uid), None)
    if item is None:
      raise RequestError(404, "未知定制项")
    if item["readOnly"]:
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
    return _guarded(self.session.snapshot)

  def session_enter(self):
    def enter():
      self.session.enter()
      return self.session.snapshot()
    return _guarded(enter)

  def session_exit(self):
    return _guarded(self.session.exit)

  # ---- 定制 ----
  def customizations_scan(self):
    def scan():
      result = self.session.run(lambda link: customization.scan(link, self._customization_items()))
      return {"items": [_customization_view(i) for i in result["items"]], "ecus": [_scan_ecu_view(e) for e in result["ecus"]]}
    return _guarded(scan)

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
    return _guarded(lambda: {"backups": [_backup_view(b) for b in self.backups.list()]})

  def backup_restore(self, backup_id: str):
    def restore():
      backup = self.backups.get(backup_id)
      if backup is None:
        raise RequestError(404, "备份不存在")
      return self.session.run(lambda link: customization.restore(link, backup, self.backups))
    return _guarded(restore)

  # ---- 故障码 ----
  def dtc_scan(self):
    return _guarded(lambda: {"ecus": self.session.run(
      lambda link: dtc.scan(link, self._dtc_targets(), catalog.load("dtc_descriptions")))})

  def dtc_clear(self, body: dict):
    def clear():
      targets = {t["ecu"]: t for t in self._dtc_targets()}
      ecus = body.get("ecus")
      if not isinstance(ecus, list) or not ecus or not all(isinstance(e, str) and e in targets for e in ecus):
        raise RequestError(400, "ecus 无效")
      return {"results": self.session.run(lambda link: [dtc.clear(link, targets[e]) for e in ecus])}
    return _guarded(clear)

  # ---- 实时数据 ----
  def _pid_profile(self) -> dict:
    profiles = self._pid_profiles()
    profile_id = self._pid_profile_id or self._default_profile_id(self._vin())
    return next((p for p in profiles if p["id"] == profile_id), profiles[0])

  def pids_get(self):
    profile = self._pid_profile()
    groups: dict[str, list[dict]] = {}
    for item in live_data.readable_pids(profile["pids"]):
      groups.setdefault(_pid_group(item), []).append({"id": item["id"], "name": catalog.text(item["name"]), "unit": item["unit"]})
    return 200, {"profile": {"id": profile["id"], "name": profile["name"]},
                 "profiles": [{"id": p["id"], "name": p["name"]} for p in self._pid_profiles()],
                 "groups": [{"ecu": ecu, "pids": pids} for ecu, pids in groups.items()]}

  def pids_select_profile(self, body: dict):
    def select():
      profile_id = body.get("id")
      if not any(p["id"] == profile_id for p in self._pid_profiles()):
        raise RequestError(400, "未知配置")
      self._pid_profile_id = profile_id
    return _guarded(select)

  def pids_read(self, body: dict):
    def read():
      ids = body.get("ids")
      if (not isinstance(ids, list) or not 0 < len(ids) <= PID_READ_LIMIT
          or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)):
        raise RequestError(400, f"ids 须为 1～{PID_READ_LIMIT} 个整数")
      pids = {p["id"]: p for p in live_data.readable_pids(self._pid_profile()["pids"])}
      return {"values": self.session.run(lambda link: live_data.read(link, pids, ids))}
    return _guarded(read)
