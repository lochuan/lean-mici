"""用户上限追踪(spec §5 语义):用户任何按压重定义上限;我们的按压不动它。"""


class CeilingTracker:
  def __init__(self, quantum_kph: float):
    self.ceiling_kph: float | None = None   # None=未知(巡航未激活)
    self._q = quantum_kph

  def on_cruise_enabled(self, set_speed_kph: float) -> None:
    self.ceiling_kph = set_speed_kph

  def on_user_set(self, new_set_speed_kph: float) -> None:
    self.ceiling_kph = new_set_speed_kph

  def on_user_bump(self, new_set_speed_kph: float) -> None:
    """用户 + 或 - :上限 = 按压后的 setSpeed。"""
    self.ceiling_kph = new_set_speed_kph

  def on_ours(self) -> None:
    pass  # 我们的按压不动上限

  def resync_if_exceeded(self, set_speed_kph: float) -> bool:
    """安全重同步:观测 setSpeed 超过上限+量子 -> 上限:=setSpeed。返回是否重同步。"""
    if self.ceiling_kph is not None and set_speed_kph > self.ceiling_kph + self._q:
      self.ceiling_kph = set_speed_kph
      return True
    return False

  def allows_up(self, set_speed_kph: float, quantum_kph: float) -> bool:
    """硬不变量查询:还能不能按 +。burst 累计由 scheduler 用 ceiling−setSpeed≥count×q 判。"""
    return self.ceiling_kph is not None and set_speed_kph < self.ceiling_kph - 1e-6
