"""统一公式 + 死区(spec §3)。全部场景都是这一个公式的特例,无场景分支代码。"""
from __future__ import annotations

from dataclasses import dataclass

from openpilot.selfdrive.cruisebuttond import constants as C

EPS = 1e-9


@dataclass(frozen=True)
class ShaperInputs:
  ceiling_kph: float | None
  set_speed_kph: float
  v_ego_kph: float
  scc_v_target_kph: float | None = None   # SCC-V 激活时,否则 None
  lead_speed_kph: float | None = None     # 雷达前车速度,无前车 None
  standstill: bool = False


def target_set_speed(inp: ShaperInputs) -> float | None:
  """None = 巡航未激活(ceiling 未知)。目标=ceiling 也返回值。"""
  if inp.ceiling_kph is None:
    return None
  candidates = [inp.ceiling_kph]
  if inp.scc_v_target_kph is not None:
    candidates.append(inp.scc_v_target_kph)
  if inp.lead_speed_kph is not None:
    candidates.append(inp.lead_speed_kph + C.MARGIN_KPH)
  return max(C.FLOOR_KPH, min(candidates))


def should_adjust(set_speed_kph: float, target_kph: float, held_s: float) -> tuple[str, float] | None:
  """死区后返回 ("down"|"up", delta_kph)。delta = |target - setSpeed|。"""
  d = target_kph - set_speed_kph
  if abs(d) <= C.DEADBAND_KPH + EPS or held_s < C.DEADBAND_S:
    return None
  return ("up" if d > 0 else "down", abs(d))
