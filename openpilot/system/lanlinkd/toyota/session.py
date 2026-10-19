"""诊断模式：临时停掉 pandad，让 lanlinkd 独占 panda 发诊断请求。

pandad 平时独占 panda，熄火时还把 safety 锁成 NO_OUTPUT，经它转发发不出诊断帧。
所以进入时置 LanLinkDiagMode，manager 据此停掉 pandad（process_config.pandad_run），
等进程真正消失后再直连 Panda()；退出（主动、空闲超时、lanlinkd 重启）时清 param，
manager 自动把 pandad 拉回来。诊断期间 openpilot 收不到 pandaStates，按熄火处理；
ELM327 safety 下线束继电器不接管，原车功能不受影响。

同一时刻只允许一个总线操作：run() 持锁执行，enter/exit 也走同一把锁。
"""
import threading
import time
from collections.abc import Callable

DIAG_MODE_PARAM = "LanLinkDiagMode"
GEAR_PARK = 1  # car.capnp GearShifter.park


class SessionError(Exception):
  def __init__(self, status: int, message: str):
    super().__init__(message)
    self.status = status
    self.message = message


def car_is_parked(status_snapshot: dict) -> bool:
  """StatusCache 快照里 carState 新鲜、挂 P 挡且静止。"""
  car = status_snapshot.get("car")
  if status_snapshot.get("stale", True) or not car:
    return False
  return car.get("gearShifter") == GEAR_PARK and bool(car.get("standstill")) and car.get("vEgo", 1.0) < 0.1


class DiagSession:
  def __init__(self, params, open_link: Callable[[], object], pandad_running: Callable[[], bool],
               is_parked: Callable[[], bool], clock: Callable[[], float] = time.time,  # noqa: TID251  lastActivity 契约是 unix 秒
               sleep: Callable[[float], None] = time.sleep, idle_timeout_s: float = 900,
               pandad_stop_timeout_s: float = 15):
    self._params = params
    self._open_link = open_link
    self._pandad_running = pandad_running
    self._is_parked = is_parked
    self._clock = clock
    self._sleep = sleep
    self.idle_timeout_s = idle_timeout_s
    self.pandad_stop_timeout_s = pandad_stop_timeout_s
    self._lock = threading.RLock()
    self._state = "idle"
    self._error = ""
    self._link = None
    self._last_activity: float | None = None

  def snapshot(self) -> dict:
    snap = {"state": self._state, "idleTimeoutS": self.idle_timeout_s}
    if self._error:
      snap["error"] = self._error
    if self._last_activity is not None:
      snap["lastActivity"] = self._last_activity
      snap["idleRemainingS"] = max(0, round(self.idle_timeout_s - (self._clock() - self._last_activity)))
    return snap

  def reset_on_startup(self) -> None:
    self._params.put_bool(DIAG_MODE_PARAM, False)

  def enter(self) -> None:
    with self._lock:
      if self._state == "active":
        return
      if not self._params.get_bool("IsOffroad") and not self._is_parked():
        raise self._fail(409, "车辆行驶中：请熄火，或挂 P 挡静止后再进入诊断模式")
      self._state, self._error = "entering", ""
      self._params.put_bool(DIAG_MODE_PARAM, True)
      deadline = self._clock() + self.pandad_stop_timeout_s
      while self._pandad_running():
        if self._clock() > deadline:
          self._params.put_bool(DIAG_MODE_PARAM, False)
          raise self._fail(504, "pandad 未能在时限内停止，已取消诊断模式")
        self._sleep(0.5)
      try:
        self._link = self._open_link()
      except Exception as exc:
        self._params.put_bool(DIAG_MODE_PARAM, False)
        raise self._fail(500, f"打开 panda 失败：{exc}") from exc
      self._state = "active"
      self._last_activity = self._clock()

  def exit(self) -> None:
    with self._lock:
      link, self._link = self._link, None
      try:
        if link is not None:
          link.close()
      finally:
        self._params.put_bool(DIAG_MODE_PARAM, False)
        self._state = "idle"
        self._last_activity = None

  def expire_if_idle(self) -> None:
    with self._lock:
      if self._state == "active" and self._clock() - self._last_activity > self.idle_timeout_s:
        self.exit()

  def run(self, operation: Callable[[object], object]):
    with self._lock:
      if self._state != "active":
        raise SessionError(409, "请先进入诊断模式")
      self._last_activity = self._clock()
      try:
        return operation(self._link)
      finally:
        self._last_activity = self._clock()

  def _fail(self, status: int, message: str) -> SessionError:
    self._state, self._error = "idle", message
    return SessionError(status, message)
