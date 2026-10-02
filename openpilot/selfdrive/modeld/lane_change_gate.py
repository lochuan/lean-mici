import time

from openpilot.common.stream_gate import StreamStatus, stream_status

# eagleState.changeClear*State -> desire_helper 清空标志:unknown(本车道线不可信)传 None 不参与门控
CHANGE_CLEAR_FLAGS = {"unknown": None, "clear": True, "blocked": False}


def change_clear_flags(state, fresh: bool) -> tuple[bool | None, bool | None]:
  if not fresh:
    return None, None
  return CHANGE_CLEAR_FLAGS[str(state.changeClearLeftState)], CHANGE_CLEAR_FLAGS[str(state.changeClearRightState)]


def run_lane_change_gate(dh, sm, lane_change_prob, left_edge, right_edge, modelv2, mdv2sp) -> None:
  # C9 变道清空门:eagleState 是 5Hz 观测流,新鲜度经观测流接收门判定
  # (fix/stream-gate,阈值/语义见 common.stream_gate)。不新鲜(从未收到/
  # 无效/超龄) -> 清空标志传 None,desire_helper 回退纯 BSM+relc 门控,
  # 绝不因感知缺失锁死变道。
  status = stream_status("eagleState",
                         None if not sm.seen['eagleState'] else time.monotonic() - sm.recv_time['eagleState'],
                         valid=sm.valid['eagleState'])
  clear_left, clear_right = change_clear_flags(sm['eagleState'], status is StreamStatus.FRESH)
  dh.update(sm['carState'], sm['carControl'].latActive, lane_change_prob, left_edge, right_edge,
            change_clear_left=clear_left, change_clear_right=clear_right)
  modelv2.meta.laneChangeState = dh.lane_change_state
  modelv2.meta.laneChangeDirection = dh.lane_change_direction
  mdv2sp.laneTurnDirection = dh.lane_turn_direction
  mdv2sp.leftLaneChangeBlock = dh.block_left
  mdv2sp.rightLaneChangeBlock = dh.block_right
  mdv2sp.laneChangeHoldReason = dh.hold_reason
