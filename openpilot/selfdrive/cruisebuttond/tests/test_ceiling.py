"""CeilingTracker 测试:用户 SET/±/巡航激活重定义上限,我们的按压不动它,
安全重同步与 allows_up 硬不变量边界。量子取 constants.DEFAULT_QUANTUM_KPH。"""

from openpilot.selfdrive.cruisebuttond import constants as C
from openpilot.selfdrive.cruisebuttond.ceiling import CeilingTracker


def test_user_set_redefines_ceiling():
  t = CeilingTracker(quantum_kph=C.DEFAULT_QUANTUM_KPH)
  t.on_user_set(75.0)
  assert t.ceiling_kph == 75.0


def test_user_bump_moves_ceiling_both_directions():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  t.on_cruise_enabled(70.0)
  t.on_user_bump(71.0)
  assert t.ceiling_kph == 71.0
  t.on_user_bump(69.0)
  assert t.ceiling_kph == 69.0


def test_cruise_enabled_sets_ceiling():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  assert t.ceiling_kph is None  # None = 未知(巡航未激活)
  t.on_cruise_enabled(65.0)
  assert t.ceiling_kph == 65.0


def test_our_presses_never_move_ceiling():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  t.on_cruise_enabled(70.0)
  t.on_ours()
  t.on_ours()
  assert t.ceiling_kph == 70.0


def test_resync_when_observed_exceeds_ceiling_plus_quantum():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  t.on_cruise_enabled(70.0)
  assert t.resync_if_exceeded(71.5) is True
  assert t.ceiling_kph == 71.5


def test_resync_requires_strictly_more_than_ceiling_plus_quantum():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  t.on_cruise_enabled(70.0)
  # 恰好上限+量子 → 不重同步
  assert t.resync_if_exceeded(71.0) is False
  assert t.ceiling_kph == 70.0
  # 未超过 → 不重同步
  assert t.resync_if_exceeded(70.5) is False


def test_resync_with_unknown_ceiling_is_noop():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  assert t.resync_if_exceeded(80.0) is False
  assert t.ceiling_kph is None


def test_allows_up_boundaries():
  t = CeilingTracker(C.DEFAULT_QUANTUM_KPH)
  # 上限未知 → 绝不允许
  assert t.allows_up(60.0, C.DEFAULT_QUANTUM_KPH) is False
  t.on_cruise_enabled(70.0)
  # 恰好在上限 → 不允许
  assert t.allows_up(70.0, C.DEFAULT_QUANTUM_KPH) is False
  # 一量子以下 → 允许
  assert t.allows_up(69.0, C.DEFAULT_QUANTUM_KPH) is True
  # 浮点容差内视为到顶 → 不允许
  assert t.allows_up(70.0 - 1e-9, C.DEFAULT_QUANTUM_KPH) is False
