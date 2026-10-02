"""间隙不足提醒接线:lateralManeuverPlan 新鲜且 valid 且 gapInsufficient 置位 → 产生事件,否则不产生。

事件本身是 warning 级(不脱离),带提示音。
"""
import pytest

from openpilot.cereal import custom
from openpilot.selfdrive.selfdrived.selfdrived import gap_insufficient_alert_active
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP, AudibleAlert

NOW = 100.0


@pytest.mark.parametrize("gap_insufficient, last_recv_s, valid, expected", [
  (True, 99.5, True, True),           # 新鲜 + valid + 置位
  (False, 99.5, True, False),         # 未置位
  (True, 99.5, False, False),         # envelope invalid
  (True, 98.0, True, False),          # 超龄(阈值 1s)
  (True, 0.0, True, False),           # 从未收到
])
def test_event_only_when_plan_is_fresh_valid_and_flagged(gap_insufficient, last_recv_s, valid, expected):
  assert gap_insufficient_alert_active(gap_insufficient, last_recv_s=last_recv_s, now=NOW, valid=valid) is expected


def test_event_is_an_audible_warning_that_never_disengages():
  alerts = EVENTS_SP[custom.OnroadEventSP.EventName.gapInsufficient]
  assert set(alerts) == {ET.WARNING}
  assert alerts[ET.WARNING].audible_alert == AudibleAlert.prompt
