"""LANLink 丰田诊断的 HTTP 适配层：参数校验 → toyota/* → (status, body)。

方法都是阻塞的（等 pandad 退出、等 ECU 应答），lanlinkd 用 asyncio.to_thread 调用。
body 为 None 表示 204。协议细节见 toyota/ 下各模块的文档字符串；前端契约类型见 web/src/lib/schema.ts。
"""
import subprocess
from collections.abc import Callable

from openpilot.common.swaglog import cloudlog
from openpilot.system.lanlinkd import vehicle_api
from openpilot.system.lanlinkd.toyota import catalog, coding, customization, dtc, live_data
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
    return 409, {"error": f"{exc}，请重新扫描"}
  except MalformedResponseError as exc:
    return 502, {"error": f"ECU 应答异常：{exc}"}
  except customization.BackupError as exc:
    return 500, {"error": str(exc)}
  except Exception as exc:  # 未预料的错误也给前端 JSON，并留日志（否则 Sanic 只回裸 500）
    cloudlog.exception("lanlink toyota: 未处理的异常")
    return 500, {"error": f"内部错误：{exc}"}
  return (204, None) if body is None else (status, body)


def _value_view(setting: dict, data_hex: str) -> dict:
  """备份里的整段数据 → {value, label}：选项型反查目录标签，数值型标签就是数字。"""
  value = coding.extract_value(bytes.fromhex(data_hex), setting["byteIndex"], int(setting["mask"], 16))
  label = next((o["label"] for o in setting["options"] if o["value"] == value), str(value))
  return {"value": value, "label": label}


def _backup_view(backup: dict) -> dict:
  setting = catalog.setting(backup["key"])
  ecu = catalog.ecu(backup["ecu"])
  return {"id": backup["id"], "time": backup["time"], "key": backup["key"], "name": setting["name"],
          "ecu": ecu["name"] if ecu else backup["ecu"],
          "before": _value_view(setting, backup["data"]), "after": _value_view(setting, backup["after"]),
          "restored": backup.get("restored", False)}


def _is_listable(backup: dict) -> bool:
  """旧格式（Car Scanner，含 uid）和目录里已没有的项都不列、不恢复。"""
  return "key" in backup and catalog.setting(backup["key"]) is not None


def _value_allowed(setting: dict, value) -> bool:
  if not isinstance(value, int) or isinstance(value, bool):
    return False
  if setting["numeric"]:
    return setting["numeric"]["min"] <= value <= setting["numeric"]["max"]
  return any(o["value"] == value for o in setting["options"])


def _pid_group(item: dict) -> str:
  return catalog.ecu_name(item["hdr"].upper(), item["sub"]) if item["cmd"] else COMPUTED_GROUP


def _catalog_ecu(body: dict) -> dict:
  ecu = catalog.ecu(body.get("ecu")) if isinstance(body.get("ecu"), str) else None
  if ecu is None:
    raise RequestError(400, "ecu 无效")
  return ecu


class ToyotaDiagApi:
  def __init__(self, session: DiagSession, backup_dir: str,
               dtc_targets: Callable[[], list[dict]] = catalog.dtc_targets,
               pid_profiles: Callable[[], list[dict]] = catalog.pid_profiles,
               vin: Callable[[], str] = lambda: "",
               default_profile_id: Callable[[str], str] = live_data.default_profile_id):
    self.session = session
    self.backups = BackupStore(backup_dir)
    self._dtc_targets = dtc_targets
    self._pid_profiles = pid_profiles
    self._vin = vin
    self._default_profile_id = default_profile_id
    self._pid_profile_id: str | None = None  # 用户手动选的配置；None = 按 VIN 年份

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

  # ---- 目录 ----
  def catalog_get(self):
    """不需要诊断模式。故障码说明不下发：扫描结果里由后端拼好。"""
    return _guarded(lambda: {k: v for k, v in catalog.load_sienna().items() if k != "dtcText"})

  # ---- 定制 ----
  def customizations_scan(self, body: dict):
    """扫一个 ECU：前端按目录 ECU 列表逐个调用，自己算进度。"""
    def scan():
      ecu = _catalog_ecu(body)
      settings = [s for s in catalog.load_sienna()["settings"] if s["ecu"] == ecu["id"]]
      return self.session.run(lambda link: customization.scan_ecu(link, ecu, settings))
    return _guarded(scan)

  def customization_write(self, key: str, body: dict):
    def write():
      setting = catalog.setting(key)
      if setting is None:
        raise RequestError(404, "未知定制项")
      value = body.get("value")
      if not _value_allowed(setting, value):
        raise RequestError(400, "value 无效")
      before = body.get("before")
      if not isinstance(before, str):
        raise RequestError(400, "缺少 before（扫描时的原始数据）")
      ecu = catalog.ecu(setting["ecu"])
      return self.session.run(lambda link: customization.write_setting(link, ecu, setting, value, before, self.backups))
    return _guarded(write)

  # ---- 备份 ----
  def backups_list(self):
    return _guarded(lambda: {"backups": [_backup_view(b) for b in self.backups.list() if _is_listable(b)]})

  def backup_restore(self, backup_id: str):
    def restore():
      backup = self.backups.get(backup_id)
      if backup is None or not _is_listable(backup):
        raise RequestError(404, "备份不存在")
      setting = catalog.setting(backup["key"])
      ecu = catalog.ecu(backup["ecu"])
      return self.session.run(lambda link: customization.restore_setting(link, ecu, setting, backup, self.backups))
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
