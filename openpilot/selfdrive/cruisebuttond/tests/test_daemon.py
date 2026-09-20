"""cruisebuttond 主循环测试:fake SubMaster 全链路(MockActuator + 模拟车辆)。

三场景(计划要求):
  1. 弯道进入/退出:SCC-V vTarget 拉低 setSpeed 走低,退出后按滑条节奏恢复
  2. 前车跟随 + 前车消失(慢车变道):贴着前车,消失后 setSpeed 停在低位按滑条节奏爬升
  3. 停车 catch-up 走正常调度器路径 + 前车起步后单拍 RES+ 自动起步(每停车episode一次)
外加:用户 SET/± 上限追踪、安全冻结(刹车/巡航关/actuator 掉线)、
量子自适应只吃 own 短按、params 门关 -> 空闲发布、向上爬升绝不越过上限。

车辆模型:MockVehicle 消费执行器命令,完成后移动 setSpeed 并在 carState 上
按 PCM 回显时序生成 press→release 事件对(归属时间线由此闭合)。
"""
from __future__ import annotations

import types

import pytest

from opendbc.car import structs
from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.actuator import MockActuator
from openpilot.selfdrive.cruisebuttond.attribution import AttributionEngine
from openpilot.selfdrive.cruisebuttond.ceiling import CeilingTracker
from openpilot.selfdrive.cruisebuttond.cruisebuttond import CruiseButtonsDaemon
from openpilot.selfdrive.cruisebuttond.scheduler import ButtonScheduler

ButtonType = structs.CarState.ButtonEvent.Type
Q = C.DEFAULT_QUANTUM_KPH
DT = 0.05  # 20Hz
VISION_ENTERING = 2  # custom.LongitudinalPlanSP.SmartCruiseControl.VisionState.entering


class _FakePubMaster:
  def __init__(self):
    self.sent = []

  def send(self, service, msg):
    self.sent.append((service, msg))

  @property
  def dbg(self):
    return self.sent[-1][1].cruiseButtonsDebug


class _FakeParams:
  def __init__(self, enabled=True, accel=None, curve=True, lead=True, standstill=True):
    self.d = {"CruiseButtonsEnabled": enabled, "CruiseButtonsCurve": curve,
              "CruiseButtonsLead": lead, "CruiseButtonsStandstill": standstill,
              "CruiseButtonsAccel": accel}

  def get_bool(self, key):
    return bool(self.d.get(key, False))

  def get(self, key, **kw):
    return self.d.get(key)


class _FakeSubMaster:
  def __init__(self, car_state, plan_sp, radar):
    self._d = {"carState": car_state, "longitudinalPlanSP": plan_sp, "radarTracks": radar}

  def update(self, timeout=0):
    pass

  def __getitem__(self, service):
    return self._d[service]


class _Radar:
  def __init__(self, points=()):
    self.points = points


class _Pt:
  def __init__(self, d_rel, lead_speed_ms, y_rel=0.5, v_ego_ms=0.0):
    self.dRel = d_rel
    self.yRel = y_rel
    # 恒速前车:vRel = 前车速度 − 自车速度(v_ego_ms 由 Harness 每帧刷新)
    self.v_ego_ms = v_ego_ms
    self.lead_speed_ms = lead_speed_ms
    self.vRel = lead_speed_ms - v_ego_ms


class MockVehicle:
  """模拟车辆:命令效果结算 + PCM 回显事件。每条命令完成时:
  setSpeed 按语义移动(向上 tap +Q×count / 向下 tap −Q / hold −duration×速率),
  carState 注入 press 事件,release 在 press_ms 后注入。
  事件时间线 > 命令发送时刻 -> 归属=ours(三重匹配窗内)。"""

  def __init__(self, sm: _FakeSubMaster, actuator: MockActuator):
    self.sm = sm
    self.actuator = actuator
    cs = sm._d["carState"]
    self.set_speed_kph = cs.cruiseState.speed
    self.v_ego_ms = cs.vEgo
    self._consumed = 0
    self.pending_presses: list = []  # 下一帧注入 buttonEvents(模拟每帧边沿流)

  def poll(self, now: float):
    # ACC 跟随:实际车速向 setSpeed 收敛(1.0 m/s² 上限,模拟原车纵向)
    err_ms = (self.set_speed_kph - self.v_ego_ms) / 3.6
    self.v_ego_ms += max(-1.0 * DT, min(1.0 * DT, err_ms / 2.0))
    self.sm._d["carState"].vEgo = self.v_ego_ms

  def poll(self, now: float):
    for cmd in self.actuator.commands[self._consumed:]:
      self._consumed += 1
      press_ms = C.TAP_PRESS_MS
      if cmd.button == "accel" and cmd.mode == "hold":
        # 长按 RES+:PCM 连续步进(每 quantum 拍一次)直到释放
        n_taps = max(1, int(cmd.duration_s / (Q / (C.HOLD_RATE_KPH_S))))
        self.set_speed_kph += Q * n_taps
        btype = ButtonType.accelCruise
        press_ms = max(cmd.duration_s * 1000.0, C.TAP_HOLD_MS)
      elif cmd.button == "accel":
        self.set_speed_kph += Q * cmd.count
        btype = ButtonType.accelCruise
      elif cmd.mode == "tap":
        self.set_speed_kph -= Q * cmd.count
        btype = ButtonType.decelCruise
      else:  # hold
        self.set_speed_kph -= C.HOLD_RATE_KPH_S * cmd.duration_s
        press_ms = max(cmd.duration_s * 1000.0, C.TAP_HOLD_MS)
        btype = ButtonType.decelCruise
      self._queue_echo(btype, press_ms)
    self.actuator.poll(now)

  def _queue_echo(self, btype, press_ms):
    self.pending_presses.append(btype)
    self.sm._d["carState"].echo_releases.append((press_ms / 1000.0, btype))


def _car_state(set_speed_kph, v_ego_ms, *, enabled=True, standstill=False, brake=False):
  return types.SimpleNamespace(
    cruiseState=types.SimpleNamespace(enabled=enabled, speed=set_speed_kph),
    vEgo=v_ego_ms, aEgo=0.0, brakePressed=brake, gasPressed=0.0,
    standstill=standstill, buttonEvents=[], echo_releases=[])


def _plan_sp():
  vs = types.SimpleNamespace(state=0, vTarget=0.0, enabled=False)
  return types.SimpleNamespace(smartCruiseControl=types.SimpleNamespace(vision=vs))


class Harness:
  def __init__(self, *, accel=1.0, enabled=True, curve=True, lead=True, standstill=True,
               set_speed=60.0, v_ego=60 / 3.6):
    self.actuator = MockActuator()
    self.params = _FakeParams(enabled=enabled, accel=accel, curve=curve,
                              lead=lead, standstill=standstill)
    self.pm = _FakePubMaster()
    self.sm = _FakeSubMaster(_car_state(set_speed, v_ego), _plan_sp(), _Radar())
    self.vehicle = MockVehicle(self.sm, self.actuator)
    attr = AttributionEngine()
    self.tracker = CeilingTracker(Q)
    sched = ButtonScheduler(self.actuator, attr, self.tracker)
    self.daemon = CruiseButtonsDaemon(sm=self.sm, pm=self.pm, params=self.params,
                                      actuator=self.actuator, attribution=attr,
                                      ceiling=self.tracker, scheduler=sched)
    self.now = 0.0
    self.daemon.update(self.now)  # 装载参数

  # --- 环境设定 ---
  def set_env(self, *, set_speed=None, v_ego=None, enabled=None, standstill=None,
              brake=None, vision_state=None, v_target_ms=None, points=None):
    cs = self.sm._d["carState"]
    if set_speed is not None:
      cs.cruiseState.speed = set_speed
    if v_ego is not None:
      cs.vEgo = v_ego
      self.vehicle.v_ego_ms = v_ego
    if enabled is not None:
      cs.cruiseState.enabled = enabled
    if standstill is not None:
      cs.standstill = standstill
    if brake is not None:
      cs.brakePressed = brake
    vs = self.sm._d["longitudinalPlanSP"].smartCruiseControl.vision
    if vision_state is not None:
      vs.state = vision_state
      vs.enabled = True
    if v_target_ms is not None:
      vs.vTarget = v_target_ms
    if points is not None:
      self.sm._d["radarTracks"] = _Radar(points)

  def refresh_lead(self):
    """恒速前车:vRel 随自车速度更新。"""
    cs = self.sm._d["carState"]
    for p in getattr(self.sm._d["radarTracks"], "points", []):
      if isinstance(p, _Pt):
        p.v_ego_ms = cs.vEgo
        p.vRel = p.lead_speed_ms - p.v_ego_ms

  def user_press(self, name: str, press_ms: float):
    """用户物理按压:直接注入回显事件(台账无对应命令 -> 归属=user)。"""
    btype = {"accel": ButtonType.accelCruise, "decel": ButtonType.decelCruise,
             "set": ButtonType.setCruise, "cancel": ButtonType.cancel}[name]
    self.vehicle.pending_presses.append(btype)
    self.sm._d["carState"].echo_releases.append((press_ms / 1000.0, btype))

  # --- 时间推进 ---
  def run(self, seconds: float):
    end = self.now + seconds
    while self.now < end - 1e-9:
      self.now += DT
      cs = self.sm._d["carState"]
      # 每帧的边沿流:上一帧 poll 排队的 press + 到期的 release
      cs.buttonEvents = [types.SimpleNamespace(type=b, pressed=True)
                         for b in self.vehicle.pending_presses]
      self.vehicle.pending_presses = []
      keep = []
      for dt, btype in cs.echo_releases:
        if dt <= DT:
          cs.buttonEvents.append(types.SimpleNamespace(type=btype, pressed=False))
        else:
          keep.append((dt - DT, btype))
      cs.echo_releases = keep
      cs.cruiseState.speed = self.vehicle.set_speed_kph
      self.refresh_lead()
      self.daemon.update(self.now)
      self.vehicle.poll(self.now)

  # --- 辅助 ---
  @property
  def commands(self):
    return self.actuator.commands

  @property
  def set_speed(self):
    return self.vehicle.set_speed_kph


# ----------------------------------------------------------- 场景 1:弯道

def test_curve_entry_descends_and_exit_recovers():
  # 用户以 60 km/h 巡航(SET),上限 = 60;弯道 SCC-V 把 setSpeed 拉低,出弯恢复
  h = Harness(set_speed=60.0, v_ego=60 / 3.6)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)  # SET 回显:上限锚定
  h.run(0.3)
  # 进弯:SCC-V 激活,vTarget=35 km/h -> 目标被拉低 -> setSpeed 向下走
  h.set_env(vision_state=VISION_ENTERING, v_target_ms=35 / 3.6)
  h.run(6.0)
  assert h.set_speed < 55.0
  assert h.pm.dbg.sccActive is True

  # 出弯:SCC-V 退出 -> 目标跳回上限,按滑条节奏向上(1 m/s² ≈ 3.6 km/h/s);
  # 死区 5 km/h:setSpeed 爬到上限−死区即停
  h.set_env(vision_state=0, v_target_ms=0.0)
  h.run(10.0)
  assert h.set_speed > 52.0
  # 绝不越过上限(硬不变量端到端)
  ceiling = h.tracker.ceiling_kph
  assert h.set_speed <= ceiling + Q + 1e-6


def test_curve_toggle_param_off_ignores_scc_target():
  h = Harness(set_speed=60.0, v_ego=60 / 3.6, curve=False)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(vision_state=VISION_ENTERING, v_target_ms=35 / 3.6)
  h.run(6.0)
  assert h.set_speed == 60.0  # 弯道开关关 -> vTarget 不参与


# ----------------------------------------------------------- 场景 2:跟车

def test_lead_follow_and_lead_disappears_walks_up_at_slider_rate():
  # 用户巡航 60;前车 45 km/h -> 跟随中 setSpeed 压到 lead+margin;慢车变道后按节奏爬回
  h = Harness(set_speed=60.0, v_ego=60 / 3.6, accel=1.0)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)
  h.run(0.3)
  # 前车 45 km/h,近距同车道 -> 目标 = lead+margin -> setSpeed 向下走到前车附近
  h.set_env(points=[_Pt(25.0, 45 / 3.6, v_ego_ms=45 / 3.6)], v_ego=45 / 3.6)
  h.run(8.0)
  lead_speed = 45
  assert abs(h.set_speed - (lead_speed + C.MARGIN_KPH)) <= C.DEADBAND_KPH + Q

  # 慢车变道消失:目标跳回上限;向上节奏由滑条决定(1 m/s² → 3.6 km/h/s)
  h.set_env(points=[])
  h.run(4.0)
  climb = h.set_speed - (lead_speed + C.MARGIN_KPH)
  assert 2.0 < climb <= 4.0 * 1.0 * 3.6 + 2 * Q  # 大于死区起步,不超节奏+容差


def test_lead_param_off_ignores_lead():
  h = Harness(set_speed=60.0, v_ego=60 / 3.6, lead=False)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(points=[_Pt(25.0, 45 / 3.6, v_ego_ms=60 / 3.6)])
  h.run(6.0)
  assert h.set_speed == 60.0  # 跟车开关关 -> 前车项不参与


# ----------------------------------------------------------- 场景 3:停车

def test_standstill_catchup_via_scheduler_then_resume_single_tap():
  # 红灯跟停:巡航设 60,车停住(PCM 保持 setSpeed=60);上限=60,余量充足
  h = Harness(set_speed=60.0, v_ego=0.0, accel=0.8)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)
  h.run(0.3)
  # 停车中:catch-up 走正常调度器路径 —— 前车静止,跟车管理把 setSpeed 压向地板(减速拍,无加速拍)
  h.set_env(standstill=True, points=[_Pt(8.0, 0.0, y_rel=0.3, v_ego_ms=0.0)])
  h.run(2.0)
  assert h.pm.dbg.standstill is True
  assert len([c for c in h.commands if c.button == "accel"]) == 0

  # 前车起步 > 0.5 m/s 持续 ≥ 1s(且跟车减速拍在途结束) -> 恰好一拍 RES+
  h.set_env(points=[_Pt(8.0, 1.2, y_rel=0.3, v_ego_ms=0.0)])
  h.run(7.0)
  resume_cmds = [c for c in h.commands if c.button == "accel" and c.mode == "tap"]
  assert len(resume_cmds) == 1
  # setSpeed 已 +Q,episode 内不再补拍
  h.run(3.0)
  resume_cmds2 = [c for c in h.commands if c.button == "accel" and c.mode == "tap"]
  assert len(resume_cmds2) == 1


def test_standstill_resume_blocked_without_headroom():
  # 停车中 setSpeed 已贴上限:无余量,不发起步
  h = Harness(set_speed=30.0, v_ego=0.0)
  h.vehicle.set_speed_kph = 30.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(standstill=True, points=[_Pt(8.0, 1.2, y_rel=0.3, v_ego_ms=0.0)])
  h.run(2.5)
  assert len([c for c in h.commands if c.button == "accel"]) == 0


def test_standstill_resume_gated_by_param():
  h = Harness(set_speed=60.0, v_ego=0.0, standstill=False)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(standstill=True, points=[_Pt(8.0, 1.2, y_rel=0.3, v_ego_ms=0.0)])
  h.run(2.5)
  assert len([c for c in h.commands if c.button == "accel"]) == 0


# ----------------------------------------------------------- 用户操作/上限

def test_user_set_redefines_ceiling_after_jump():
  h = Harness(set_speed=60.0, v_ego=60 / 3.6)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)          # 用户 SET
  h.run(0.2)
  h.vehicle.set_speed_kph = 75.0      # 车辆跳变到新 setSpeed(观察窗内)
  h.run(0.5)
  assert h.tracker.ceiling_kph == 75.0


def test_user_bump_plus_raises_ceiling_by_quantum():
  h = Harness(set_speed=60.0, v_ego=60 / 3.6)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("accel", 100.0)
  h.run(0.2)
  assert h.tracker.ceiling_kph == 60.0 + Q


def test_user_bump_minus_follows_set_speed_down():
  h = Harness(set_speed=60.0, v_ego=60 / 3.6)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("decel", 100.0)
  h.run(0.2)
  h.vehicle.set_speed_kph = 57.0
  h.run(0.5)
  assert h.tracker.ceiling_kph == 57.0
  # 之后车辆继续降,上限单调跟随
  h.vehicle.set_speed_kph = 54.0
  h.run(0.5)
  assert h.tracker.ceiling_kph == 54.0


def test_our_taps_never_move_ceiling():
  # 用户巡航 50;目标=上限 50?不 —— SET 后上限锚定在 50,降低场景才有向上差。
  # 这里:SET 后用户 − 压低 setSpeed,上限仍 50;出弯目标回 50,我们向上按,上限不动
  h = Harness(set_speed=50.0, v_ego=50 / 3.6)
  h.vehicle.set_speed_kph = 50.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(points=[_Pt(20.0, 40 / 3.6, v_ego_ms=40 / 3.6)], v_ego=40 / 3.6)  # 前车把 setSpeed 拉低
  h.run(5.0)
  assert h.set_speed < 48.0
  h.set_env(points=[])  # 前车消失,目标回上限;死区内停手(45 = 50 − 死区)
  h.run(8.0)
  assert h.set_speed >= 45.0  # 已按死区边界恢复
  assert h.tracker.ceiling_kph == 50.0  # 我们的按压不动上限


def test_quantum_adaptation_feeds_only_ours_taps():
  # 我们自己的 tap 后车辆按 Q 步进 -> 探测到 delta=Q,quantum 保持默认
  h = Harness(set_speed=50.0, v_ego=50 / 3.6)
  h.vehicle.set_speed_kph = 50.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(points=[_Pt(20.0, 0.4, -5 / 3.6)], v_ego=40 / 3.6)
  h.run(5.0)
  h.set_env(points=[])
  h.run(4.0)
  assert h.daemon.scheduler.quantum_kph == pytest.approx(Q)


# ----------------------------------------------------------- 安全冻结

def test_brake_freezes_and_aborts():
  h = Harness(set_speed=50.0, v_ego=50 / 3.6)
  h.vehicle.set_speed_kph = 50.0
  h.user_press("set", 100.0)
  h.run(0.3)
  h.set_env(points=[_Pt(20.0, 0.4, -5 / 3.6)], v_ego=40 / 3.6)
  h.run(3.0)
  assert len(h.commands) >= 1
  h.set_env(brake=True)
  h.run(0.3)
  assert h.pm.dbg.frozen is True
  n = len(h.commands)
  h.run(1.0)
  assert len(h.commands) == n  # 冻结期间不再发命令


def test_cruise_off_clears_ceiling_and_freezes():
  h = Harness(set_speed=50.0, v_ego=50 / 3.6)
  h.vehicle.set_speed_kph = 50.0
  h.run(2.0)
  h.set_env(enabled=False)
  h.run(0.3)
  assert h.pm.dbg.frozen is True
  assert h.tracker.ceiling_kph is None


def test_actuator_disconnect_freezes():
  h = Harness(set_speed=50.0, v_ego=50 / 3.6)
  h.actuator._fault = True
  h.actuator._state.connected = False
  h.run(0.3)
  assert h.pm.dbg.frozen is True
  assert h.pm.dbg.btConnected is False


# ----------------------------------------------------------- 门与发布

def test_master_gate_off_publishes_idle_state():
  h = Harness(enabled=False, set_speed=60.0, v_ego=60 / 3.6)
  h.run(1.0)
  assert h.pm.dbg.enabled is False
  assert h.pm.dbg.frozen is False
  assert len(h.commands) == 0


def test_debug_message_has_full_state_every_frame():
  h = Harness(set_speed=60.0, v_ego=60 / 3.6)
  h.vehicle.set_speed_kph = 60.0
  h.user_press("set", 100.0)
  h.run(0.5)
  dbg = h.pm.dbg
  for field in ("enabled", "btConnected", "ceilingKph", "targetKph", "setSpeedKph",
                "vEgoKph", "quantumKph", "sccActive", "leadPresent", "standstill",
                "lastButton", "lastEchoOurs", "unexplained", "frozen"):
    assert hasattr(dbg, field), field
  assert dbg.enabled is True
  assert dbg.btConnected is True
  assert dbg.ceilingKph == pytest.approx(60.0)
  assert dbg.vEgoKph == pytest.approx(60.0, rel=0.01)
