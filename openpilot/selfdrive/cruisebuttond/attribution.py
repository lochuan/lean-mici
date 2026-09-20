"""按压归属:回显本身无法区分用户与模拟器(同一物理电路),靠三重时间线:
命令(t0,seq) -> 模拟器ACK(t1) -> 回显(t2)。归属不明按用户处理(安全方向:
低估上限只是恢复慢,高估上限会超出用户意图)。
"""
from __future__ import annotations

from dataclasses import dataclass

from openpilot.selfdrive.cruisebuttond import constants as C


@dataclass(frozen=True)
class ButtonEcho:
  button: str        # "accel"|"decel"|"set"|"cancel"
  pressed: bool      # True=按下边沿
  mono_time: float


@dataclass(frozen=True)
class CommandRecord:
  seq: int
  button: str        # "accel"|"decel"
  t_send: float
  taps: int          # burst 拍数


class _Ledger:
  """命令台账:已发未匹配命令的有界列表,两种归属模式共用。"""

  _LEDGER_TTL_S = 30.0

  def __init__(self, window_s: float):
    self._window = window_s
    self._pending: list[CommandRecord] = []
    self.unexplained: int = 0  # 连续无法解释的回显数

  def register(self, rec: CommandRecord) -> None:
    self._pending.append(rec)
    # 有界:只留最近 30s,防台账无限增长
    self._pending = [r for r in self._pending if r.t_send > rec.t_send - self._LEDGER_TTL_S]

  def match(self, button: str, now: float) -> CommandRecord | None:
    """窗口内最早的同按钮命令;命中即消耗(多拍 burst 每拍一条记录)。"""
    for i, rec in enumerate(self._pending):
      if (rec.button == button
          and rec.t_send <= now <= rec.t_send + self._window):
        self._pending.pop(i)
        return rec
    return None

  def any_pending_in_window(self, now: float) -> bool:
    return any(rec.t_send <= now <= rec.t_send + self._window for rec in self._pending)

  def reset(self) -> None:
    self._pending.clear()
    self.unexplained = 0


class AttributionEngine:
  """可插拔:回显模式(首选)/差分模式(回显不可用时的回退)。两者对同一接口。"""

  def __init__(self, window_s: float = C.ATTRIBUTION_WINDOW_S):
    self._ledger = _Ledger(window_s)
    self._last_press_verdict: dict[str, str] = {}  # button -> 最近一次 press 判定

  @property
  def unexplained(self) -> int:
    return self._ledger.unexplained

  @property
  def _pending(self) -> list[CommandRecord]:
    return self._ledger._pending

  def on_command(self, rec: CommandRecord) -> None:
    self._ledger.register(rec)

  def classify_echo(self, echo: ButtonEcho) -> str:
    """返回 "ours" | "user"。仅对 press 边沿计数;release 边沿跟随前一判定,
    由调用方经 verdict_for_release 配对(此处维护 button -> last_verdict)。"""
    if echo.pressed:
      if self._ledger.match(echo.button, echo.mono_time) is not None:
        self._ledger.unexplained = 0
        verdict = "ours"
      else:
        self._ledger.unexplained += 1
        verdict = "user"
      self._last_press_verdict[echo.button] = verdict
      return verdict
    return "user"   # release 边沿不参与归属判定

  def verdict_for_release(self, echo: ButtonEcho) -> str:
    """release 边沿的判定 = 该按钮最近一次 press 的判定;无记录按 user。"""
    return self._last_press_verdict.get(echo.button, "user")

  def reset(self) -> None:
    self._ledger.reset()
    self._last_press_verdict.clear()


class DeltaAttribution:
  """回退模式:无回显时按 setSpeed 变化窗口归属。幅度分类:
  跳变(≈vEgo 或 >3 量子)= SET;±1 量子 = 加减。接口与 AttributionEngine 相同。"""

  def __init__(self, window_s: float = C.ATTRIBUTION_WINDOW_S,
               quantum_kph: float = C.DEFAULT_QUANTUM_KPH):
    self._ledger = _Ledger(window_s)
    self._q = quantum_kph

  @property
  def unexplained(self) -> int:
    return self._ledger.unexplained

  @property
  def _pending(self) -> list[CommandRecord]:
    return self._ledger._pending

  def on_command(self, rec: CommandRecord) -> None:
    self._ledger.register(rec)

  def classify_set_speed(self, old_kph: float, new_kph: float, now: float) -> str:
    """返回 "ours" | "user" | "ambiguous"。窗口内且 |Δ| 与登记命令的预期吻合
    (按钮方向一致、±1 量子)→ ours;窗口外 → user;其余 → ambiguous(按 user 处理)。"""
    delta = new_kph - old_kph
    magnitude = abs(delta)
    if magnitude > 3.0 * self._q:
      kind = "set"        # 跳变:我们绝不会引发
    elif 0.5 * self._q <= magnitude <= 1.5 * self._q:
      kind = "bump"       # ±1 量子
    else:
      kind = "ambiguous"  # 2–3 量子之间或无变化
    if not self._any_pending_in_window(now):
      if magnitude < 0.5 * self._q:
        return "ambiguous"  # 无有效变化,非事件
      self._ledger.unexplained += 1
      return "user"         # 窗口外 → 用户
    if kind == "bump":
      if self._ledger.match(_button_for(delta), now) is not None:
        self._ledger.unexplained = 0
        return "ours"
    return "ambiguous"      # 窗口内但幅度/方向与命令预期不符

  def _any_pending_in_window(self, now: float) -> bool:
    return self._ledger.any_pending_in_window(now)

  def reset(self) -> None:
    self._ledger.reset()


def _button_for(delta_kph: float) -> str:
  return "accel" if delta_kph > 0 else "decel"
