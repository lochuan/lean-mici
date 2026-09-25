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
  assert "change_clear_left=eagle_state.changeClearLeft if eagle_fresh else None" in src
  assert "change_clear_right=eagle_state.changeClearRight if eagle_fresh else None" in src
