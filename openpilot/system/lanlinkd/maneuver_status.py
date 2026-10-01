"""避让/变道「没执行的原因」复算（纯函数，输入全是已读出的值）。

不改 capnp、不改 desire_helper：按 avoidance_planner.update 的 gated 条件和
desire_helper.update 的 preLaneChange 门控顺序，用公开消息字段复算。
近似项（desire_helper 内部状态未发布）：brake / waiting / notArmed。
"""
from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.controls.lib.desire_helper import LANE_CHANGE_SPEED_MIN

ALC_OFF = -1
ALC_NUDGE = 0


def avoid_block_reason(dbg: dict, avoidance_enabled: bool, steering_pressed: bool, lane_change_state: str) -> str | None:
  """避让已迟滞激活但本帧没执行的原因；正在执行或无目标返回 None。"""
  if not dbg.get("active"):
    return None
  if dbg.get("valid") and dbg.get("maxOffset", 0.0) > 0.0:
    return None
  if not avoidance_enabled:
    return "disabled"
  if steering_pressed:
    return "steering"
  if lane_change_state != "off":
    return "laneChange"
  if not C.V_EGO_MIN <= dbg.get("vEgo", 0.0) <= C.V_EGO_MAX:
    return "speed"
  if dbg.get("edgeClearance", 999.0) < C.EDGE_CLEAR_MIN:
    return "edge"
  if dbg.get("maxOffset", 0.0) <= 0.0:
    return "budget"
  return "unknown"


def lane_change_status(state: str, direction: str, car: dict, edge_block: tuple[bool, bool],
                       change_clear: tuple[bool | None, bool | None], alc_mode: int) -> dict:
  """car: leftBlinker/rightBlinker/leftBlindspot/rightBlindspot/brakePressed/steeringPressed/vEgo。"""
  left = direction == "left"
  reason = None
  if state == "preLaneChange" and direction in ("left", "right"):
    side = 0 if left else 1
    if car["leftBlindspot" if left else "rightBlindspot"]:
      reason = "blindspot"
    elif edge_block[side]:
      reason = "roadEdge"
    elif change_clear[side] is False:
      reason = "notClear"
    elif alc_mode == ALC_NUDGE:
      reason = "nudge"
    elif car["brakePressed"]:
      reason = "brake"
    else:
      reason = "waiting"
  elif state == "off" and car["leftBlinker"] != car["rightBlinker"]:
    if alc_mode == ALC_OFF:
      reason = "alcOff"
    elif car["vEgo"] < LANE_CHANGE_SPEED_MIN:
      reason = "belowSpeed"
    else:
      reason = "notArmed"
  return {
    "state": state,
    "direction": direction,
    "blinkerLeft": car["leftBlinker"], "blinkerRight": car["rightBlinker"],
    "blindspotLeft": car["leftBlindspot"], "blindspotRight": car["rightBlindspot"],
    "edgeBlockLeft": edge_block[0], "edgeBlockRight": edge_block[1],
    "changeClearLeft": change_clear[0], "changeClearRight": change_clear[1],
    "alcMode": alc_mode,
    "brakePressed": car["brakePressed"], "steeringPressed": car["steeringPressed"],
    "vEgo": car["vEgo"],
    "blockReason": reason,
  }
