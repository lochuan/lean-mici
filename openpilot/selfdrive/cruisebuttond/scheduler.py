"""按压调度:目标差 -> 命令序列。硬不变量两层重复检查(spec §6)。

节奏为时间驱动:每帧最多排一拍,绝不排长队。向上拍间隔由加速度滑条导出,
向下短按固定节奏、大落差单次长按。偏移帽双向;上限不变量在调度器层
(_press_up)与执行器层(_send)各查一次,纵深防御。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.actuator import PressCommand
from openpilot.selfdrive.cruisebuttond.attribution import CommandRecord
from openpilot.selfdrive.cruisebuttond.ceiling import CeilingTracker
from openpilot.selfdrive.cruisebuttond.shaper import ShaperInputs, should_adjust, target_set_speed

EPS = 1e-9


@dataclass
class SchedulerConfig:
  accel_ms2: float = C.DEFAULT_ACCEL_MS2
  curve_on: bool = True      # 三个场景开关:CruiseButtonsCurve/Lead/Standstill
  lead_on: bool = True
  standstill_on: bool = True


class ButtonScheduler:
  def __init__(self, actuator, attribution, ceiling: CeilingTracker):
    self.actuator = actuator
    self.attribution = attribution
    self.ceiling = ceiling
    self.quantum_kph = C.DEFAULT_QUANTUM_KPH
    self._last_cmd_t = -1e9   # 首帧即可按压(节奏时钟从 -inf 起步)
    self._recent_tap_deltas: list[float] = []   # 量子自适应,最近 10 个中位数
    self._seq = 0
    self._set_speed_kph: float | None = None    # daemon 每帧喂入(update)
    self._press_times: dict[str, list[float]] = {"accel": [], "decel": []}  # 分方向限幅滑窗

  # --- 量子自适应(运行时,非学习) ---
  def observe_confirmed_tap(self, delta_kph: float) -> None:
    """归属为'ours'的短按 + 其后 setSpeed 变化 -> 收集;中位数更新 quantum。"""
    self._recent_tap_deltas.append(abs(delta_kph))
    self._recent_tap_deltas = self._recent_tap_deltas[-10:]
    if len(self._recent_tap_deltas) >= 3:
      self.quantum_kph = sorted(self._recent_tap_deltas)[len(self._recent_tap_deltas) // 2]

  # --- 主决策(每帧) ---
  def update(self, inp: ShaperInputs, cfg: SchedulerConfig, held_s: float, now: float) -> None:
    self._set_speed_kph = inp.set_speed_kph
    if not cfg.curve_on:
      inp = replace(inp, scc_v_target_kph=None)
    if not cfg.lead_on:
      inp = replace(inp, lead_speed_kph=None)
    target = target_set_speed(inp)
    if target is None:
      self._maybe_abort()
      return
    if inp.standstill and not cfg.standstill_on:
      return
    direction_delta = should_adjust(inp.set_speed_kph, target, held_s)
    if direction_delta is None:
      return
    direction, delta = direction_delta
    if direction == "down":
      self._press_down(delta, inp, now)
    else:
      self._press_up(delta, inp, cfg, now)

  def _press_up(self, delta, inp: ShaperInputs, cfg: SchedulerConfig, now: float) -> None:
    # 硬不变量 #1(调度器层):绝不越过上限,含 burst 累计
    if not self.ceiling.allows_up(inp.set_speed_kph, self.quantum_kph):
      return
    headroom = self.ceiling.ceiling_kph - inp.set_speed_kph
    # 偏移帽:向上一路前查
    if inp.set_speed_kph - inp.v_ego_kph >= C.OFFSET_CAP_KPH - EPS:
      return
    # 节奏:滑条 -> 爬升率 -> 拍间隔;本帧最多排一拍(节奏由时间驱动,不排长队)
    rate_kph_s = cfg.accel_ms2 * 3.6
    interval = max(C.MIN_CMD_INTERVAL_S, self.quantum_kph / rate_kph_s)
    if now - self._last_cmd_t < interval - EPS:
      return
    count = 1   # 一拍一命令,节奏自持;大 delta 由后续帧继续
    if headroom < self.quantum_kph * count - EPS:
      return  # 拍不下就不按
    self._send(PressCommand("accel", "tap", count, interval, C.TAP_PRESS_MS / 1000, self._next_seq()), now)

  def _press_down(self, delta, inp: ShaperInputs, now: float) -> None:
    # 偏移帽:向下一路前查(vEgo 在 setSpeed 下方超过帽 -> 停手等车)
    if inp.v_ego_kph - inp.set_speed_kph >= C.OFFSET_CAP_KPH - EPS:
      return
    if delta <= 3 * self.quantum_kph + EPS:
      if now - self._last_cmd_t < C.DECEL_TAP_INTERVAL_S - EPS:
        return
      self._send(PressCommand("decel", "tap", 1, C.DECEL_TAP_INTERVAL_S, C.TAP_PRESS_MS / 1000, self._next_seq()), now)
    else:
      # 大落差用长按:duration = 剩余/假设速率;上车标定 HOLD_RATE。
      # concurrent-press 防抖:长按在途或距上条命令过近时不叠发。
      if self.actuator.state().executing or now - self._last_cmd_t < C.MIN_CMD_INTERVAL_S - EPS:
        return
      duration = min(delta / C.HOLD_RATE_KPH_S, 2.0)
      self._send(PressCommand("decel", "hold", 1, 0.0, duration, self._next_seq()), now)

  def _send(self, cmd: PressCommand, now: float) -> None:
    # 硬不变量 #1(执行器层):最后一道闸
    if cmd.button == "accel":
      ceiling = self.ceiling.ceiling_kph
      if ceiling is None or self._set_speed_kph is None or \
         self._set_speed_kph + self.quantum_kph * cmd.count > ceiling + EPS:
        return
    # 节奏限幅(spec §6.4,按方向分预算):延迟减速是危险方向,预算独立。
    # 加速预算严格(防失控上冲循环);减速预算高于自然节奏上限,仅拦真失控。
    self._press_times[cmd.button] = [t for t in self._press_times[cmd.button] if t > now - 60.0]
    limit = C.MAX_ACCEL_PRESSES_PER_MIN if cmd.button == "accel" else C.MAX_DECEL_PRESSES_PER_MIN
    if len(self._press_times[cmd.button]) >= limit:
      return
    self._press_times[cmd.button].append(now)
    self.actuator.press(cmd)
    # 每拍一条记录(per-tap 契约:多拍 burst = 多条,归属消耗才正确)
    self.attribution.on_command(CommandRecord(cmd.seq, cmd.button, now, cmd.count))
    self._last_cmd_t = now

  def _next_seq(self) -> int:
    seq = self._seq
    self._seq = (self._seq + 1) % 256  # 帧内 seq 为 u8,回绕
    return seq

  def _maybe_abort(self) -> None:
    if self.actuator.state().executing:
      self.actuator.abort()
