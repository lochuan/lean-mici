"""ButtonScheduler 测试:节奏数学、偏移帽、两层硬不变量、abort 语义、
量子自适应、每拍归属登记、限速(spec §3/§6)。"""

import random

import pytest

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.actuator import MockActuator
from openpilot.selfdrive.cruisebuttond.attribution import AttributionEngine
from openpilot.selfdrive.cruisebuttond.ceiling import CeilingTracker
from openpilot.selfdrive.cruisebuttond.scheduler import ButtonScheduler, SchedulerConfig
from openpilot.selfdrive.cruisebuttond.shaper import ShaperInputs


def make_sched(ceiling=100.0):
  mock = MockActuator()
  attr = AttributionEngine()
  tracker = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  tracker.ceiling_kph = ceiling
  return ButtonScheduler(mock, attr, tracker), mock, attr, tracker


def inp(ceiling=100.0, set_speed=60.0, v_ego=60.0, scc=None, lead=None, standstill=False):
  return ShaperInputs(ceiling, set_speed, v_ego, scc, lead, standstill)


# --- 向上节奏:滑条 -> 爬升率 -> 拍间隔 ---


def test_up_interval_from_accel_slider():
  s, mock, _, _ = make_sched()
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(accel_ms2=1.0), held_s=10.0, now=100.0)
  assert len(mock.commands) == 1
  cmd = mock.commands[0]
  assert (cmd.button, cmd.mode, cmd.count) == ("accel", "tap", 1)
  assert cmd.interval_s == pytest.approx(1.0 / (1.0 * 3.6))  # quantum / (a*3.6)


def test_up_interval_floors_at_min_cmd_interval():
  s, mock, _, _ = make_sched()
  for _ in range(3):
    s.observe_confirmed_tap(0.3)
  assert s.quantum_kph == pytest.approx(0.3)
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(accel_ms2=2.0), held_s=10.0, now=100.0)
  assert mock.commands[0].interval_s == pytest.approx(C.MIN_CMD_INTERVAL_S)


def test_up_rhythm_is_time_driven_one_press_per_frame():
  s, mock, _, _ = make_sched()
  cfg = SchedulerConfig(accel_ms2=1.0)
  s.update(inp(set_speed=50.0, v_ego=50.0), cfg, held_s=10.0, now=100.0)
  s.update(inp(set_speed=50.0, v_ego=50.0), cfg, held_s=10.0, now=100.05)  # 间隔内不再按
  assert len(mock.commands) == 1
  s.update(inp(set_speed=50.0, v_ego=50.0), cfg, held_s=10.0, now=100.0 + 1.0 / 3.6 + 1e-3)
  assert len(mock.commands) == 2


# --- 量子自适应 ---


def test_quantum_adaptation_needs_three_samples_and_takes_median():
  s, _, _, _ = make_sched()
  s.observe_confirmed_tap(1.0)
  s.observe_confirmed_tap(1.0)
  assert s.quantum_kph == C.DEFAULT_QUANTUM_KPH  # <3 样本不更新
  s.observe_confirmed_tap(2.0)
  assert s.quantum_kph == pytest.approx(1.0)  # median(1,1,2)


def test_quantum_adaptation_uses_last_ten():
  s, _, _, _ = make_sched()
  for _ in range(9):
    s.observe_confirmed_tap(1.0)
  s.observe_confirmed_tap(5.0)  # 滚出窗口外的影响:median(1×9,5)=1
  assert s.quantum_kph == pytest.approx(1.0)
  for _ in range(10):
    s.observe_confirmed_tap(1.6)
  assert s.quantum_kph == pytest.approx(1.6)


def test_mph_quantum_widens_interval():
  s, mock, _, _ = make_sched()
  for _ in range(3):
    s.observe_confirmed_tap(1.6)
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(accel_ms2=1.0), held_s=10.0, now=100.0)
  assert s.quantum_kph == pytest.approx(1.6)
  assert mock.commands[0].interval_s == pytest.approx(1.6 / 3.6)


# --- 死区(经调度器全路径)---


def test_deadband_distance_boundary_in_scheduler():
  s, mock, _, _ = make_sched()
  # lead 62 -> target 65,d = 5.0 恰在死区 -> 不动手
  s.update(inp(set_speed=60.0, v_ego=60.0, lead=62.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert mock.commands == []
  # lead 62.1 -> target 65.1,d = 5.1 -> 动手
  s.update(inp(set_speed=60.0, v_ego=60.0, lead=62.1), SchedulerConfig(), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1


def test_deadband_time_boundary_in_scheduler():
  s, mock, _, _ = make_sched()
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=C.DEADBAND_S - 0.1, now=100.0)
  assert mock.commands == []
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=C.DEADBAND_S, now=100.5)
  assert len(mock.commands) == 1


# --- 向下:短按节奏与长按公式 ---


def test_decel_tap_rhythm_with_large_quantum():
  s, mock, _, _ = make_sched()
  for _ in range(3):
    s.observe_confirmed_tap(2.0)
  # delta 6 <= 3×2 -> 短按,固定 0.8s 节奏
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=61.0), SchedulerConfig(), held_s=10.0, now=100.0)
  cmd = mock.commands[0]
  assert (cmd.button, cmd.mode) == ("decel", "tap")
  assert cmd.interval_s == pytest.approx(C.DECEL_TAP_INTERVAL_S)
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=61.0), SchedulerConfig(), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1  # 节奏内不再按
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=61.0), SchedulerConfig(), held_s=10.0, now=100.85)
  assert len(mock.commands) == 2


def test_hold_duration_formula_and_cap():
  s, mock, _, _ = make_sched()
  # delta 37 -> 37/8 = 4.625s -> 封顶 2.0s
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=30.0), SchedulerConfig(), held_s=10.0, now=100.0)
  cmd = mock.commands[0]
  assert (cmd.button, cmd.mode, cmd.count) == ("decel", "hold", 1)
  assert cmd.duration_s == pytest.approx(2.0)
  # 未封顶:delta 9 -> 1.125s
  s2, mock2, _, _ = make_sched()
  s2.update(inp(set_speed=80.0, v_ego=80.0, lead=68.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert mock2.commands[0].mode == "hold"
  assert mock2.commands[0].duration_s == pytest.approx(9.0 / C.HOLD_RATE_KPH_S)


def test_hold_waits_for_in_flight_press():
  s, mock, _, _ = make_sched()
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=30.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert mock.commands[0].mode == "hold"
  mock._state.executing = True  # 长按在途
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=30.0), SchedulerConfig(), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1  # 在途时不叠发


# --- 偏移帽(双向,恰好在帽上即停)---


def test_offset_cap_up_blocks_at_and_beyond():
  s, mock, _, _ = make_sched()
  s.update(inp(set_speed=65.0, v_ego=55.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert mock.commands == []  # set - v = 10.0 恰在帽 -> 停手
  s.update(inp(set_speed=65.0, v_ego=55.5), SchedulerConfig(), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1 and mock.commands[0].button == "accel"


def test_offset_cap_down_blocks_at_and_beyond():
  s, mock, _, _ = make_sched()
  s.update(inp(set_speed=50.0, v_ego=60.0, lead=40.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert mock.commands == []  # v - set = 10.0 -> 停手等车
  s.update(inp(set_speed=50.0, v_ego=59.5, lead=40.0), SchedulerConfig(), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1 and mock.commands[0].button == "decel"


# --- 硬不变量两层 ---


def test_layer1_blocks_at_and_above_ceiling():
  s, mock, _, _ = make_sched(ceiling=80.0)
  cfg = SchedulerConfig()
  s._press_up(10.0, inp(ceiling=80.0, set_speed=80.0, v_ego=80.0), cfg, now=100.0)
  s._press_up(10.0, inp(ceiling=80.0, set_speed=85.0, v_ego=85.0), cfg, now=100.0)
  assert mock.commands == []


def test_no_press_when_headroom_below_quantum():
  s, mock, _, _ = make_sched(ceiling=80.0)
  cfg = SchedulerConfig()
  s._set_speed_kph = 79.5
  s._press_up(10.0, inp(ceiling=80.0, set_speed=79.5, v_ego=79.5), cfg, now=100.0)
  assert mock.commands == []  # headroom 0.5 < 1 量子,拍不下就不按
  s._set_speed_kph = 79.0
  s._press_up(10.0, inp(ceiling=80.0, set_speed=79.0, v_ego=79.0), cfg, now=100.0)
  assert len(mock.commands) == 1


def test_send_second_layer_gate_blocks_when_first_bypassed():
  s, mock, _, _ = make_sched(ceiling=100.0)
  cfg = SchedulerConfig()
  # crafted:daemon 喂入的 setSpeed 已贴近上限,而 inp.set_speed 更低(第一层放行)
  s._set_speed_kph = 99.5
  s._press_up(10.0, inp(ceiling=100.0, set_speed=90.0, v_ego=90.0), cfg, now=100.0)
  assert mock.commands == []           # 99.5 + 1 > 100 -> 最后一道闸拦下
  assert s.attribution._pending == []  # 未登记台账
  # 恰好到顶允许:99 + 1 == 100
  s._set_speed_kph = 99.0
  s._press_up(10.0, inp(ceiling=100.0, set_speed=90.0, v_ego=90.0), cfg, now=100.5)
  assert len(mock.commands) == 1


# --- 归属登记(per-tap 契约)与 seq ---


def test_each_press_registers_one_command_record():
  s, mock, attr, _ = make_sched()
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=10.0, now=100.0)
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=10.0, now=100.5)
  assert len(mock.commands) == 2
  assert len(attr._pending) == 2  # 每拍一条记录
  rec = attr._pending[0]
  assert rec.button == "accel" and rec.taps == 1 and rec.t_send == pytest.approx(100.0)
  assert rec.seq == mock.commands[0].seq


def test_seq_wraps_at_256():
  s, mock, _, _ = make_sched()
  s._seq = 255
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=10.0, now=100.0)
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=10.0, now=100.5)
  assert [c.seq for c in mock.commands] == [255, 0]


# --- 节奏限幅(spec §6.4,分方向预算)---


def test_rate_limited_to_max_accel_presses_per_min():
  s, mock, _, _ = make_sched(ceiling=200.0)
  cfg = SchedulerConfig(accel_ms2=2.0)
  now = 100.0
  for _ in range(120):
    s.update(inp(ceiling=200.0, set_speed=50.0, v_ego=50.0), cfg, held_s=10.0, now=now)
    now += 0.15  # > MIN_CMD_INTERVAL_S,但 120 拍/18s 远超加速预算 60/min
  assert len(mock.commands) == C.MAX_ACCEL_PRESSES_PER_MIN


def test_rate_limited_to_max_decel_presses_per_min():
  s, mock, _, _ = make_sched()
  now = 100.0
  for _ in range(150):
    # 每帧 0.4s 间隔 > DECEL_TAP_INTERVAL_S;150 拍/60s 超 120/min 减速预算
    s.update(inp(ceiling=200.0, set_speed=100.0, v_ego=100.0, lead=50.0), SchedulerConfig(), held_s=10.0, now=now)
    now += 0.4
  assert len(mock.commands) == C.MAX_DECEL_PRESSES_PER_MIN


def test_saturated_accel_budget_does_not_block_decel():
  s, mock, _, _ = make_sched(ceiling=200.0)
  cfg = SchedulerConfig(accel_ms2=2.0)
  now = 100.0
  for _ in range(C.MAX_ACCEL_PRESSES_PER_MIN):
    s.update(inp(ceiling=200.0, set_speed=50.0, v_ego=50.0), cfg, held_s=10.0, now=now)
    now += 0.15
  n_accel = len(mock.commands)
  assert n_accel == C.MAX_ACCEL_PRESSES_PER_MIN  # 加速预算完全耗尽
  # 弯道切入:减速必须立即发出,不受加速预算影响
  s.update(inp(ceiling=200.0, set_speed=100.0, v_ego=100.0, scc=60.0), cfg, held_s=10.0, now=now)
  assert len(mock.commands) == n_accel + 1
  assert mock.commands[-1].button == "decel"


def test_hold_debounce_delayed_retry_counts_once():
  s, mock, _, _ = make_sched()
  # 首帧:长按发出并登记一次
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=30.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert len(mock.commands) == 1 and mock.commands[0].mode == "hold"
  mock._state.executing = True  # 长按在途 -> 防抖拦下重试
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=30.0), SchedulerConfig(), held_s=10.0, now=100.2)
  assert len(mock.commands) == 1 and len(s._press_times["decel"]) == 1  # 未多计数
  # 在途结束后的下一帧重试:仍防抖(距上条 < MIN_CMD_INTERVAL_S 不可能,这里已过 0.2s)
  mock._state.executing = False
  s.update(inp(set_speed=70.0, v_ego=70.0, lead=30.0), SchedulerConfig(), held_s=10.0, now=100.4)
  # delta 仍 37 -> 新长按(上一次已耗尽),限幅窗口只加一条
  assert len(mock.commands) == 2
  assert len(s._press_times["decel"]) == 2


# --- abort 语义(spec §6.2)---


def test_cruise_off_aborts_in_flight_and_clears_pending():
  s, mock, _, tracker = make_sched(ceiling=100.0)
  s.update(inp(set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=10.0, now=100.0)
  assert len(mock.commands) == 1
  mock._now = 100.0
  mock._burst = None  # tap 瞬时完成;手工构造一个在途 burst
  mock._state.executing = True
  s.update(inp(ceiling=None, set_speed=50.0, v_ego=50.0), SchedulerConfig(), held_s=10.0, now=100.1)
  assert mock.abort_calls == 1
  assert not mock.state().executing and mock.state().pending == 0


def test_only_accel_or_decel_ever_sent():
  s, mock, _, _ = make_sched()
  now = 100.0
  for lead in (30.0, None):
    s.update(inp(ceiling=100.0, set_speed=50.0, v_ego=50.0, lead=lead), SchedulerConfig(), held_s=10.0, now=now)
    now += 1.0
  assert mock.commands
  assert {c.button for c in mock.commands} <= {"accel", "decel"}


# --- 场景开关 ---


def test_curve_switch_gates_scc():
  s, mock, _, _ = make_sched(ceiling=65.0)
  # scc 40:关 -> 目标 65,d=5 死区 -> 不动;开 -> 目标 40 -> 向下
  s.update(inp(ceiling=65.0, set_speed=60.0, v_ego=60.0, scc=40.0),
           SchedulerConfig(curve_on=False), held_s=10.0, now=100.0)
  assert mock.commands == []
  s.update(inp(ceiling=65.0, set_speed=60.0, v_ego=60.0, scc=40.0),
           SchedulerConfig(curve_on=True), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1 and mock.commands[0].button == "decel"


def test_lead_switch_gates_lead():
  s, mock, _, _ = make_sched(ceiling=100.0)
  # lead 关 -> 目标跳回上限 100 -> 向上(慢车消失同款公式路径)
  s.update(inp(ceiling=100.0, set_speed=60.0, v_ego=60.0, lead=40.0),
           SchedulerConfig(lead_on=False), held_s=10.0, now=100.0)
  assert len(mock.commands) == 1 and mock.commands[0].button == "accel"
  # lead 开 -> 目标 43 -> 向下
  s2, mock2, _, _ = make_sched()
  s2.update(inp(ceiling=100.0, set_speed=60.0, v_ego=60.0, lead=40.0),
            SchedulerConfig(lead_on=True), held_s=10.0, now=100.0)
  assert mock2.commands[0].button == "decel"


def test_standstill_switch_gates_all_presses_at_standstill():
  s, mock, _, _ = make_sched()
  frame = inp(ceiling=100.0, set_speed=50.0, v_ego=0.0, scc=35.0, standstill=True)
  s.update(frame, SchedulerConfig(standstill_on=False), held_s=10.0, now=100.0)
  assert mock.commands == []
  s.update(frame, SchedulerConfig(standstill_on=True), held_s=10.0, now=100.5)
  assert len(mock.commands) == 1 and mock.commands[0].button == "decel"


# --- 对抗性确定性帧 ---


def test_adversarial_frames_never_violate_invariants():
  s, mock, _, tracker = make_sched()
  cfg = SchedulerConfig()
  cases = [
    inp(ceiling=60.0, set_speed=60.0, v_ego=60.0),   # 恰在上限
    inp(ceiling=60.0, set_speed=70.0, v_ego=60.0),   # 超上限
    inp(ceiling=60.0, set_speed=50.0, v_ego=20.0),   # vEgo 远低于 setSpeed(向上帽)
    inp(ceiling=20.0, set_speed=15.0, v_ego=10.0),   # FLOOR 把目标顶到上限之上
    inp(ceiling=100.0, set_speed=95.0, v_ego=95.0),  # 贴近上限
  ]
  now = 100.0
  for frame in cases:
    tracker.ceiling_kph = frame.ceiling_kph
    n0 = len(mock.commands)
    s.update(frame, cfg, held_s=10.0, now=now)
    now += 0.5
    for cmd in mock.commands[n0:]:
      if cmd.button == "accel":
        assert frame.set_speed_kph + cmd.count * s.quantum_kph <= frame.ceiling_kph + 1e-6
        assert frame.set_speed_kph - frame.v_ego_kph < C.OFFSET_CAP_KPH
      else:
        assert frame.v_ego_kph - frame.set_speed_kph < C.OFFSET_CAP_KPH


# --- 不变量属性(模糊)测试:最重要的测试 ---


def test_fuzz_invariant_property():
  """≥1000 随机帧:任何发出的 accel 都满足 setSpeed + count×quantum ≤ ceiling,
  偏移帽双向成立,巡航关断 abort 清空在途,绝不发 cancel/distance。"""
  rng = random.Random(0xC0FFEE)
  mock = MockActuator()
  attr = AttributionEngine()
  tracker = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  s = ButtonScheduler(mock, attr, tracker)
  now = 1000.0
  accel_cmds = 0
  decel_cmds = 0
  for i in range(1200):
    ceiling = None if rng.random() < 0.05 else round(rng.uniform(30.0, 130.0), 1)
    set_speed = round(rng.uniform(0.0, 140.0), 1)
    v_ego = round(rng.uniform(0.0, 140.0), 1)
    lead = None if rng.random() < 0.4 else round(rng.uniform(0.0, 120.0), 1)
    scc = None if rng.random() < 0.4 else round(rng.uniform(0.0, 120.0), 1)
    standstill = rng.random() < 0.1
    held = rng.uniform(0.0, 10.0)
    cfg = SchedulerConfig(
      accel_ms2=rng.uniform(0.3, 2.0),
      curve_on=rng.random() < 0.85,
      lead_on=rng.random() < 0.85,
      standstill_on=rng.random() < 0.85)
    if rng.random() < 0.05:
      s.observe_confirmed_tap(rng.uniform(0.5, 2.0))
    tracker.ceiling_kph = ceiling
    frame = ShaperInputs(ceiling, set_speed, v_ego, scc, lead, standstill)
    executing_before = mock.state().executing
    n0 = len(mock.commands)
    s.update(frame, cfg, held_s=held, now=now)
    for cmd in mock.commands[n0:]:
      assert cmd.button in ("accel", "decel"), (i, cmd)
      assert cmd.mode in ("tap", "hold"), (i, cmd)
      assert cmd.count == 1, (i, cmd)
      if cmd.button == "accel":
        accel_cmds += 1
        assert ceiling is not None, (i, cmd, frame)
        assert set_speed + cmd.count * s.quantum_kph <= ceiling + 1e-6, (i, cmd, frame)
        assert set_speed - v_ego < C.OFFSET_CAP_KPH, (i, cmd, frame)
      else:
        decel_cmds += 1
        assert v_ego - set_speed < C.OFFSET_CAP_KPH, (i, cmd, frame)
    if ceiling is None and executing_before:
      assert mock.abort_calls >= 1, i
      assert not mock.state().executing and mock.state().pending == 0, i
    mock.poll(now + 0.05)  # 驱动 ACK:让 burst 进入 executing,下一帧可触发 abort 路径
    now += rng.uniform(0.02, 0.6)
  assert accel_cmds > 0, "fuzz never exercised the up path"
  assert decel_cmds > 0, "fuzz never exercised the down path"
