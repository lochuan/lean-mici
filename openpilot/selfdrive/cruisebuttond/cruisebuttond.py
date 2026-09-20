"""cruisebuttond 主循环(Task 6):20Hz 把归属/上限/公式/调度器组装成守护进程。

照 avoidanced 房风格:DI 构造(sm/pm/params/actuator 可注入),realtime + Ratekeeper(20)。
订阅 carState(按钮回显/巡航状态)、longitudinalPlanSP(SCC-V vTarget)、radarTracks(前车);
每帧发布 cruiseButtonsDebug。安全红线:绝不发 CAN,唯一执行器是蓝牙模拟按钮。
"""
from __future__ import annotations

import time

from opendbc.car import structs
import openpilot.cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, Ratekeeper, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.common.constants import CV
from openpilot.cereal import custom
from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.actuator import ActuatorState, BluetoothActuator, PressCommand
from openpilot.selfdrive.cruisebuttond.attribution import AttributionEngine, ButtonEcho, CommandRecord
from openpilot.selfdrive.cruisebuttond.ceiling import CeilingTracker
from openpilot.selfdrive.cruisebuttond.scheduler import ButtonScheduler, SchedulerConfig
from openpilot.selfdrive.cruisebuttond.shaper import ShaperInputs, target_set_speed, EPS

PARAMS_REFRESH_PERIOD = 1.0  # s,照 avoidanced _refresh_params

# 回显按钮类型 -> 归属引擎按钮名(PCM_CRUISE_4 映射,opendbc carstate 已转成标准 ButtonEvent)
_BUTTON_NAMES = {
  structs.CarState.ButtonEvent.Type.accelCruise: "accel",
  structs.CarState.ButtonEvent.Type.decelCruise: "decel",
  structs.CarState.ButtonEvent.Type.setCruise: "set",
  structs.CarState.ButtonEvent.Type.cancel: "cancel",
}

_ACTIVE_SCC_STATES = (
  custom.LongitudinalPlanSP.SmartCruiseControl.VisionState.entering,
  custom.LongitudinalPlanSP.SmartCruiseControl.VisionState.turning,
  custom.LongitudinalPlanSP.SmartCruiseControl.VisionState.leaving,
)

# 用户 SET 后 setSpeed 跳变的观察窗;± 按压后 setSpeed 收敛窗(保守放宽到 2×归属窗)
_USER_SET_WINDOW_S = 2.0
_USER_BUMP_WINDOW_S = 3.0
# 我们 own 短按回显后,观察 setSpeed 变化以喂量子自适应的时长
_QUANTUM_PROBE_S = 1.2
# CANCEL 回显后的冻结时长
_CANCEL_FREEZE_S = 5.0
# 最近命令显示窗(debug 语义:上次我们命令的按钮,5s 内保留)
_ATTRIB_RECENT_S = 5.0


class CruiseButtonsDaemon:
  def __init__(self, sm=None, pm=None, params=None, actuator=None,
               attribution=None, ceiling=None, scheduler=None):
    self.params = params if params is not None else Params()
    self.sm = sm if sm is not None else messaging.SubMaster(
      ['carState', 'longitudinalPlanSP', 'radarTracks'])
    self.pm = pm if pm is not None else messaging.PubMaster(['cruiseButtonsDebug'])
    self.actuator = actuator if actuator is not None else BluetoothActuator()
    self.attribution = attribution if attribution is not None else AttributionEngine()
    self.ceiling = ceiling if ceiling is not None else CeilingTracker(C.DEFAULT_QUANTUM_KPH)
    self.scheduler = scheduler if scheduler is not None else \
      ButtonScheduler(self.actuator, self.attribution, self.ceiling)

    self.cfg = SchedulerConfig()
    self.enabled = False          # CruiseButtonsEnabled 主开关(观测值)
    self._last_params_t = -PARAMS_REFRESH_PERIOD

    # 安全冻结:frozen = live 条件(刹车/巡航关/未连接) ∨ 粘性条件(unexplained/cancel 窗)
    self._cancel_frozen_until = -1e9
    self._unexplained_frozen = False
    # 回显 press→release 配对:button -> (t_press, verdict)
    self._open_press: dict[str, tuple[float, str]] = {}
    self._prev_cruise_enabled = False
    self._held_reset_t: float | None = None
    # 用户操作窗
    self._user_set_until = -1e9
    self._user_set_applied: float | None = None
    self._user_bump_until = -1e9
    # 量子探测:own 短按回显时刻 + 回显时 setSpeed
    self._probe: tuple[float, float] | None = None
    # standstill 起步
    self._go_since: float | None = None
    self._resumed_this_stop = False
    # 发布辅助
    self.last_button: int = 255   # 0=RES+ 1=RES- 255=none(我们最近命令)
    self.last_echo_ours = False
    self.target_kph: float | None = None
    self.scc_active = False
    self.lead_present = False
    self.lead_speed_kph: float | None = None
    # log-once(照 avoidanced _degrade)
    self._logged: set[str] = set()

  def _log_once(self, reason: str) -> None:
    if reason not in self._logged:
      self._logged.add(reason)
      cloudlog.warning(f"cruisebuttond: {reason}")

  # --- params(1s 刷新,avoidanced 模式) ---
  def _refresh_params(self, now: float) -> None:
    if now - self._last_params_t < PARAMS_REFRESH_PERIOD:
      return
    self._last_params_t = now
    enabled = self.params.get_bool("CruiseButtonsEnabled")
    if enabled and not self.enabled:
      self._on_feature_enabled()
    if not enabled and self.enabled:
      self._on_feature_disabled()
    self.enabled = enabled
    try:
      value = self.params.get("CruiseButtonsAccel")
      accel = float(value) if value not in (None, "") else C.DEFAULT_ACCEL_MS2
    except (TypeError, ValueError):
      accel = C.DEFAULT_ACCEL_MS2
    self.cfg.accel_ms2 = accel
    self.cfg.curve_on = self.params.get_bool("CruiseButtonsCurve")
    self.cfg.lead_on = self.params.get_bool("CruiseButtonsLead")
    self.cfg.standstill_on = self.params.get_bool("CruiseButtonsStandstill")

  def _on_feature_enabled(self) -> None:
    self._reset_runtime()

  def _on_feature_disabled(self) -> None:
    self._reset_runtime()
    self.actuator.abort()

  def _reset_runtime(self) -> None:
    self.attribution.reset()
    self.ceiling.ceiling_kph = None
    self._cancel_frozen_until = -1e9
    self._unexplained_frozen = False
    self._open_press.clear()
    self._user_set_until = -1e9
    self._user_bump_until = -1e9
    self._probe = None
    self._go_since = None
    self._resumed_this_stop = False
    self._held_reset_t = None
    self.last_button = 255
    self.last_echo_ours = False

  # --- 回显事件流 ---
  def _process_buttons(self, button_events, now: float, set_speed_kph: float) -> None:
    for ev in button_events:
      button = _BUTTON_NAMES.get(ev.type)
      if button is None:
        continue
      if ev.pressed:
        verdict = self.attribution.classify_echo(ButtonEcho(button, True, now))
        self._open_press[button] = (now, verdict)
        self.last_echo_ours = verdict == "ours"
        if verdict == "ours" and button in ("accel", "decel"):
          self._probe = (now, set_speed_kph)
        if button == "cancel":
          # 我们从不发 CANCEL;任何 CANCEL 回显都来自用户/车机 -> 冻结观察
          self._cancel_frozen_until = now + _CANCEL_FREEZE_S
      else:
        open_press = self._open_press.pop(button, None)
        if open_press is None:
          continue
        t_press, press_verdict = open_press
        duration_ms = (now - t_press) * 1000.0
        tap = duration_ms < C.TAP_HOLD_MS  # 短按/长按分类(台账配对语义)
        if press_verdict == "user":
          self._on_user_press(button, tap, now, set_speed_kph)

  def _on_user_press(self, button: str, tap: bool, now: float, set_speed_kph: float) -> None:
    if button == "set":
      # SET:上限 = 新 setSpeed(观察窗内跟随跳变,一次即锁)
      self._user_set_until = now + _USER_SET_WINDOW_S
      self._user_set_applied = None
    elif button == "accel" and self.ceiling.ceiling_kph is not None:
      # 用户 +:上限 += 量子(短按/长按一致,保守)
      self.ceiling.on_user_bump(self.ceiling.ceiling_kph + self.scheduler.quantum_kph)
      self._user_bump_until = -1e9  # + 走量子步进,不跟踪观察窗
    elif button == "decel":
      # 用户 −/长按:上限 = 按压后的 setSpeed(观察窗内单调下降跟随)
      self._user_bump_until = now + _USER_BUMP_WINDOW_S

  def _apply_user_windows(self, now: float, set_speed_kph: float) -> None:
    if self._user_set_until > now:
      if self._user_set_applied is None:
        self._user_set_applied = set_speed_kph
        self.ceiling.on_user_set(set_speed_kph)
      elif abs(set_speed_kph - self._user_set_applied) > 0.5 * self.scheduler.quantum_kph:
        self.ceiling.on_user_set(set_speed_kph)
        self._user_set_until = -1e9  # 跳变已捕获,锁住
    elif self._user_bump_until > now and self.ceiling.ceiling_kph is not None:
      if set_speed_kph < self.ceiling.ceiling_kph:
        self.ceiling.on_user_bump(set_speed_kph)  # − 方向单调下降,安全

  def _feed_quantum_probe(self, now: float, set_speed_kph: float) -> None:
    if self._probe is None:
      return
    t_echo, ss_at_echo = self._probe
    if now - t_echo < _QUANTUM_PROBE_S:
      return
    delta = set_speed_kph - ss_at_echo
    self._probe = None
    # 只喂 own 短按;幅度合理才收(用户并发按压污染时丢弃)
    if 0.2 * self.scheduler.quantum_kph < delta < 3.0 * self.scheduler.quantum_kph:
      self.scheduler.observe_confirmed_tap(delta)

  # --- 场景输入 ---
  def _scc_v_target_kph(self) -> tuple[bool, float | None]:
    try:
      vision = self.sm['longitudinalPlanSP'].smartCruiseControl.vision
    except (KeyError, AttributeError):
      return False, None
    active = vision.enabled and vision.state in _ACTIVE_SCC_STATES
    if active:
      return True, float(vision.vTarget) * CV.MS_TO_KPH
    return False, None

  def _lead(self, v_ego_ms: float) -> tuple[bool, float | None]:
    try:
      points = self.sm['radarTracks'].points
    except (KeyError, AttributeError):
      return False, None
    best = None
    for p in points:
      if p.dRel > 0 and abs(p.yRel) <= C.LEAD_MAX_Y_ABS_M:
        if best is None or p.dRel < best.dRel:
          best = p
    if best is None:
      return False, None
    return True, (v_ego_ms + float(best.vRel)) * CV.MS_TO_KPH

  # --- 安全冻结 ---
  def _freeze_reasons(self, now: float, car_state, cruise_enabled: bool,
                      act_state: ActuatorState) -> list[str]:
    reasons: list[str] = []
    if car_state.brakePressed:
      reasons.append("brake")
    if not cruise_enabled:
      reasons.append("cruise_off")
    if not act_state.connected:
      reasons.append("actuator_disconnected")
    if act_state.fault:
      reasons.append("actuator_fault")
    if self.attribution.unexplained >= C.UNEXPLAINED_FREEZE:
      self._unexplained_frozen = True
    if self._unexplained_frozen:
      reasons.append("unexplained")
    if now < self._cancel_frozen_until:
      reasons.append("cancel")
    return reasons

  # --- standstill 起步(绕过调度器节奏,单拍直发) ---
  def _standstill_resume(self, now: float, car_state, cruise_enabled: bool,
                         frozen: bool, set_speed_kph: float) -> None:
    act_state = self.actuator.state()
    lead_go = self.lead_present and self.lead_speed_kph is not None and \
      self.lead_speed_kph * CV.KPH_TO_MS > C.LEAD_MIN_SPEED_MS
    go = (car_state.standstill and not car_state.brakePressed and cruise_enabled
          and not frozen and self.cfg.standstill_on and lead_go
          and act_state.connected and not act_state.executing
          and not self._resumed_this_stop
          and self.ceiling.ceiling_kph is not None
          and set_speed_kph < self.ceiling.ceiling_kph - EPS)  # 上限余量检查
    if not go:
      self._go_since = None
      if not car_state.standstill:
        self._resumed_this_stop = False  # 停车episode结束,重新武装
      return
    if self._go_since is None:
      self._go_since = now
      return
    if now - self._go_since < C.STANDSTILL_RESUME_DEBOUNCE_S:
      return
    # 一拍 RES+,直接到执行器(不经调度器节奏 —— Task 5 偏移帽会拦住它,这是设计使然)
    cmd = PressCommand("accel", "tap", 1, 0.0, C.TAP_PRESS_MS / 1000.0, self.scheduler._next_seq())
    self.actuator.press(cmd)
    self.attribution.on_command(CommandRecord(cmd.seq, "accel", now, 1))
    self.scheduler._last_cmd_t = now
    self.last_button = 0
    self._probe = (now, set_speed_kph)
    self._resumed_this_stop = True
    self._go_since = None

  # --- 发布 ---
  def _publish(self, frozen: bool, standstill: bool, set_speed_kph: float,
               v_ego_kph: float) -> None:
    msg = messaging.new_message('cruiseButtonsDebug')
    dbg = msg.cruiseButtonsDebug
    act_state = self.actuator.state()
    dbg.enabled = self.enabled
    dbg.btConnected = act_state.connected
    dbg.btState = (2 if act_state.fault else 1 if act_state.executing else 0)
    dbg.ceilingKph = float(self.ceiling.ceiling_kph) if self.ceiling.ceiling_kph is not None else 0.0
    dbg.targetKph = float(self.target_kph) if self.target_kph is not None else 0.0
    dbg.setSpeedKph = float(set_speed_kph)
    dbg.vEgoKph = float(v_ego_kph)
    dbg.quantumKph = float(self.scheduler.quantum_kph)
    dbg.sccActive = self.scc_active
    dbg.leadPresent = self.lead_present
    dbg.leadSpeedKph = float(self.lead_speed_kph) if self.lead_speed_kph is not None else 0.0
    dbg.standstill = standstill
    dbg.lastButton = self.last_button
    dbg.lastEchoOurs = self.last_echo_ours
    dbg.unexplained = min(self.attribution.unexplained, 255)
    dbg.frozen = frozen
    msg.valid = True
    self.pm.send('cruiseButtonsDebug', msg)

  # --- 主帧 ---
  def update(self, now: float) -> None:
    self._refresh_params(now)
    self.sm.update(0)
    car_state = self.sm['carState']
    set_speed_kph = float(car_state.cruiseState.speed)
    v_ego_ms = float(car_state.vEgo)
    v_ego_kph = v_ego_ms * CV.MS_TO_KPH
    cruise_enabled = bool(car_state.cruiseState.enabled)

    if not self.enabled:
      # 主开关关:空闲发布(前端可见"未启用"),不做任何逻辑
      self.actuator.poll(now)
      self.target_kph = None
      self._publish(frozen=False, standstill=bool(car_state.standstill),
                    set_speed_kph=set_speed_kph, v_ego_kph=v_ego_kph)
      return

    self.actuator.poll(now)
    act_state = self.actuator.state()
    if not act_state.connected:
      self._log_once("蓝牙模拟器未连接(未配对或硬件未到),守护进程空转发布状态")

    # 巡航激活沿 -> 上限 = setSpeed
    if cruise_enabled and not self._prev_cruise_enabled:
      self.ceiling.on_cruise_enabled(set_speed_kph)
      self._resumed_this_stop = False
      self._unexplained_frozen = False
    self._prev_cruise_enabled = cruise_enabled
    if not cruise_enabled:
      self.ceiling.ceiling_kph = None

    self._process_buttons(car_state.buttonEvents, now, set_speed_kph)
    self._apply_user_windows(now, set_speed_kph)
    self.ceiling.resync_if_exceeded(set_speed_kph)
    self._feed_quantum_probe(now, set_speed_kph)

    freeze_reasons = self._freeze_reasons(now, car_state, cruise_enabled, act_state)
    frozen = bool(freeze_reasons)

    # 场景输入 -> 公式
    self.scc_active, scc_target = self._scc_v_target_kph()
    self.lead_present, self.lead_speed_kph = self._lead(v_ego_ms)
    inp = ShaperInputs(
      ceiling_kph=self.ceiling.ceiling_kph,
      set_speed_kph=set_speed_kph,
      v_ego_kph=v_ego_kph,
      scc_v_target_kph=scc_target,
      lead_speed_kph=self.lead_speed_kph,
      standstill=bool(car_state.standstill),
    )
    self.target_kph = target_set_speed(inp)

    if not frozen:
      # 死区保持计时:|setSpeed−target| 持续超出死区才动手
      held_s = self._held_s(inp, now)
      self.scheduler.update(inp, self.cfg, held_s, now)
      self._standstill_resume(now, car_state, cruise_enabled, frozen, set_speed_kph)
    else:
      self._on_freeze(freeze_reasons)
      self._go_since = None

    # 观测最近命令按钮(debug):照 scheduler 命令时间戳
    self.last_button = 255
    last_accel = max(self.scheduler._press_times.get("accel", []) or [-1e18])
    last_decel = max(self.scheduler._press_times.get("decel", []) or [-1e18])
    if max(last_accel, last_decel) > now - _ATTRIB_RECENT_S:
      self.last_button = 0 if last_accel >= last_decel else 1

    self._publish(frozen, bool(car_state.standstill), set_speed_kph, v_ego_kph)

  def _held_s(self, inp: ShaperInputs, now: float) -> float:
    """死区保持时长:目标差持续超出死区的秒数;回到死区内清零。"""
    if self.target_kph is None:
      self._held_reset_t = now
      return 0.0
    if abs(inp.set_speed_kph - self.target_kph) > C.DEADBAND_KPH:
      if getattr(self, '_held_reset_t', None) is None:
        self._held_reset_t = now
      return now - self._held_reset_t
    self._held_reset_t = now
    return 0.0

  def _on_freeze(self, reasons: list[str]) -> None:
    # 立即停手:abort 清空模拟器在途 burst + 归属台账复位(unexplained 粘性位已置,
    # 复位不影响冻结判定;巡航重新激活时 _reset_runtime 清粘性位)
    self.actuator.abort()
    self.attribution.reset()


def main() -> None:
  config_realtime_process([0, 1, 2, 3], Priority.CTRL_LOW)
  cloudlog.info("cruisebuttond starting")
  daemon = CruiseButtonsDaemon()
  rk = Ratekeeper(20.0)
  while True:
    daemon.update(time.monotonic())
    rk.keep_time()


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    cloudlog.warning("got SIGINT")
