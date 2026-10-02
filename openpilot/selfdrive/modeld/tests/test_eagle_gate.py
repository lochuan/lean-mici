"""modeld 的 eagleState 接收门接线断言（fix/stream-gate）。

C9 变道清空门消费 eagleState 时必须走 common.stream_gate 的新鲜度判定，
不新鲜（从未收到/无效/超龄）清空标志传 None 让 desire_helper 回退 BSM+relc。
modeld 主循环需要 QCOM GPU 无法在 CI 构造（同 test_is_run_model 的理由），
这里做源码级接线断言：判定若从 stream_status 跑回内联算式，本测试会红。
"""
import inspect

from openpilot.selfdrive.modeld import modeld


def test_eagle_gate_routes_through_stream_gate():
  src = inspect.getsource(modeld)
  assert 'stream_status("eagleState"' in src, \
    "modeld 的 eagleState 新鲜度判定必须走 common.stream_gate（勿手写 age 算式）"
  assert "eagle_fresh = eagle_status is StreamStatus.FRESH" in src


def test_eagle_gate_falls_back_to_none_flags():
  # 不新鲜 → 清空标志传 None（desire_helper 的回退语义），不能透传 stale 标志
  src = inspect.getsource(modeld)
  assert "CHANGE_CLEAR_FLAGS[str(eagle_state.changeClearLeftState)] if eagle_fresh else None" in src
  assert "CHANGE_CLEAR_FLAGS[str(eagle_state.changeClearRightState)] if eagle_fresh else None" in src


def test_unknown_clear_state_maps_to_none():
  # 本车道线不可信 → unknown → None（不参与门控，回退 BSM + relc）
  assert modeld.CHANGE_CLEAR_FLAGS == {"unknown": None, "clear": True, "blocked": False}


def test_block_reasons_published_to_model_data_sp():
  src = inspect.getsource(modeld)
  for line in ("modelDataV2SP.leftLaneChangeBlock = DH.block_left",
               "modelDataV2SP.rightLaneChangeBlock = DH.block_right",
               "modelDataV2SP.laneChangeHoldReason = DH.hold_reason"):
    assert line in src
