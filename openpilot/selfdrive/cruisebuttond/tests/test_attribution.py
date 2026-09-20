"""归属引擎测试:回显三重匹配、窗口边界、同时按压、ACK 丢失、多拍消耗、
unexplained 计数与复位、release 边沿配对;差分回退的幅度分类。
常数来自 constants.py(Task 2),绝不在此重定义。"""

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.attribution import (
  AttributionEngine,
  ButtonEcho,
  CommandRecord,
  DeltaAttribution,
)


def _rec(seq=1, button="accel", t_send=100.0, taps=1):
  return CommandRecord(seq=seq, button=button, t_send=t_send, taps=taps)


def _echo(button="accel", pressed=True, mono_time=100.0):
  return ButtonEcho(button=button, pressed=pressed, mono_time=mono_time)


# ------------------------------------------------ 回显模式:三重匹配

def test_matched_echo_is_ours():
  eng = AttributionEngine()
  eng.on_command(_rec(t_send=100.0))
  assert eng.classify_echo(_echo(mono_time=100.5)) == "ours"
  assert eng.unexplained == 0


def test_window_boundary_inside_and_outside():
  # 恰好在窗口右边界上 → 闭区间,匹配
  eng = AttributionEngine()
  eng.on_command(_rec(t_send=100.0))
  assert eng.classify_echo(_echo(mono_time=100.0 + C.ATTRIBUTION_WINDOW_S)) == "ours"

  # 窗口外一点点 → 用户
  eng2 = AttributionEngine()
  eng2.on_command(_rec(t_send=100.0))
  assert eng2.classify_echo(_echo(mono_time=100.0 + C.ATTRIBUTION_WINDOW_S + 1e-3)) == "user"
  assert eng2.unexplained == 1


def test_button_mismatch_is_user():
  eng = AttributionEngine()
  eng.on_command(_rec(button="accel"))
  assert eng.classify_echo(_echo(button="decel")) == "user"
  assert eng.unexplained == 1


def test_ack_lost_echo_without_command_is_user():
  eng = AttributionEngine()
  assert eng.classify_echo(_echo()) == "user"
  assert eng.unexplained == 1


def test_user_set_cancel_echoes_do_not_count_unexplained():
  # 用户 SET/CANCEL 是正常驾驶操作(我们从不命令这两个按钮):不计入 unexplained,
  # 否则三次普通用户操作就会把功能冻成"模拟器故障"
  eng = AttributionEngine()
  assert eng.classify_echo(_echo(button="set", mono_time=100.0)) == "user"
  assert eng.classify_echo(_echo(button="cancel", mono_time=101.0)) == "user"
  assert eng.classify_echo(_echo(button="set", mono_time=102.0)) == "user"
  assert eng.unexplained == 0


def test_unexplained_counts_only_accel_decel_toward_freeze_threshold():
  # ± 是我们模拟的按钮:无法解释的 ± 回显才可能是模拟器误按,阈值语义不变
  eng = AttributionEngine()
  for i in range(C.UNEXPLAINED_FREEZE):
    assert eng.classify_echo(_echo(button="accel", mono_time=100.0 + i)) == "user"
  assert eng.unexplained == C.UNEXPLAINED_FREEZE


def test_simultaneous_user_and_ours():
  eng = AttributionEngine()
  eng.on_command(_rec(button="accel", t_send=100.0))
  # 用户同时按 decel(台账无命令)→ user;我们的 accel 回显 → ours
  assert eng.classify_echo(_echo(button="decel", mono_time=100.2)) == "user"
  assert eng.unexplained == 1
  assert eng.classify_echo(_echo(button="accel", mono_time=100.4)) == "ours"
  assert eng.unexplained == 0


def test_multi_tap_burst_consumes_one_record_per_tap():
  eng = AttributionEngine()
  # scheduler 对 burst 每拍登记一条记录,每条回显消耗一条
  eng.on_command(_rec(seq=1, t_send=100.0))
  eng.on_command(_rec(seq=2, t_send=100.8))
  assert eng.classify_echo(_echo(mono_time=100.1)) == "ours"
  assert eng.classify_echo(_echo(mono_time=100.9)) == "ours"
  # 第三拍回显无命令 → user
  assert eng.classify_echo(_echo(mono_time=101.7)) == "user"
  assert eng.unexplained == 1


def test_unexplained_resets_on_match():
  eng = AttributionEngine()
  eng.classify_echo(_echo(mono_time=1.0))
  eng.classify_echo(_echo(mono_time=2.0))
  assert eng.unexplained == 2
  eng.on_command(_rec(t_send=3.0))
  assert eng.classify_echo(_echo(mono_time=3.1)) == "ours"
  assert eng.unexplained == 0


def test_first_match_consumes_earliest_command():
  eng = AttributionEngine()
  eng.on_command(_rec(seq=1, t_send=100.0))
  eng.on_command(_rec(seq=2, t_send=100.1))
  assert eng.classify_echo(_echo(mono_time=100.05)) == "ours"
  assert eng.classify_echo(_echo(mono_time=100.15)) == "ours"


def test_ledger_is_bounded():
  eng = AttributionEngine()
  for s in range(1000):
    eng.on_command(_rec(seq=s, t_send=1000.0 + s))
  assert len(eng._pending) < 1000  # 30s 有界,不随命令数无限增长


def test_reset_clears_pending_and_counter():
  eng = AttributionEngine()
  eng.on_command(_rec())
  eng.classify_echo(_echo(button="decel"))
  eng.reset()
  assert eng.unexplained == 0
  assert eng.classify_echo(_echo(mono_time=100.0)) == "user"  # 台账已清


# ------------------------------------------------ release 边沿配对

def test_release_edge_not_counted_and_paired_with_press_verdict():
  eng = AttributionEngine()
  eng.on_command(_rec(t_send=100.0))
  assert eng.classify_echo(_echo(pressed=True, mono_time=100.1)) == "ours"
  # release 边沿:不参与归属判定,不消耗命令、不计 unexplained
  release = _echo(pressed=False, mono_time=100.3)
  assert eng.classify_echo(release) == "user"
  assert eng.unexplained == 0
  # 配对:release 跟随该按钮最近一次 press 的判定
  assert eng.verdict_for_release(release) == "ours"


def test_release_after_user_press_pairs_user():
  eng = AttributionEngine()
  assert eng.classify_echo(_echo(pressed=True, mono_time=100.1)) == "user"
  assert eng.verdict_for_release(_echo(pressed=False, mono_time=100.3)) == "user"


def test_release_without_press_defaults_user():
  eng = AttributionEngine()
  assert eng.verdict_for_release(_echo(button="decel", pressed=False)) == "user"


# ------------------------------------------------ 差分回退模式

def test_delta_bump_in_window_is_ours():
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  assert eng.classify_set_speed(60.0, 61.0, now=100.3) == "ours"
  assert eng.unexplained == 0


def test_delta_bump_outside_window_is_user():
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  assert eng.classify_set_speed(60.0, 61.0, now=100.0 + C.ATTRIBUTION_WINDOW_S + 0.5) == "user"
  assert eng.unexplained == 1


def test_delta_three_magnitude_classes():
  # ±1 量子 = 加减类:窗口内且方向吻合 → ours
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  assert eng.classify_set_speed(60.0, 61.0, now=100.3) == "ours"
  # 2–3 量子之间 → 不明:窗口内但幅度与命令预期不符(按 user 处理)
  eng2 = DeltaAttribution()
  eng2.on_command(_rec(button="accel", t_send=100.0))
  assert eng2.classify_set_speed(61.0, 63.0, now=100.5) == "ambiguous"
  # 跳变(>3 量子)= SET 类:我们绝不会引发 → 窗口内也不算 ours
  eng3 = DeltaAttribution()
  eng3.on_command(_rec(button="accel", t_send=100.0))
  assert eng3.classify_set_speed(63.0, 78.0, now=100.7) == "ambiguous"


def test_delta_set_jump_outside_window_is_user():
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  assert eng.classify_set_speed(50.0, 65.0, now=102.0) == "user"


def test_delta_direction_mismatch_is_ambiguous():
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  # 窗口内但是减量,与 accel 命令预期不符 → ambiguous
  assert eng.classify_set_speed(60.0, 59.0, now=100.3) == "ambiguous"


def test_delta_no_command_bump_is_user():
  eng = DeltaAttribution()
  assert eng.classify_set_speed(60.0, 61.0, now=100.0) == "user"
  assert eng.unexplained == 1


def test_delta_multi_tap_consumption():
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  eng.on_command(_rec(button="accel", t_send=100.8))
  assert eng.classify_set_speed(60.0, 61.0, now=100.2) == "ours"
  assert eng.classify_set_speed(61.0, 62.0, now=101.0) == "ours"
  assert eng.classify_set_speed(62.0, 63.0, now=101.5) == "user"


def test_delta_zero_delta_is_ambiguous():
  eng = DeltaAttribution()
  eng.on_command(_rec(button="accel", t_send=100.0))
  assert eng.classify_set_speed(60.0, 60.0, now=100.3) == "ambiguous"
  assert len(eng._pending) == 1  # 不消耗命令
