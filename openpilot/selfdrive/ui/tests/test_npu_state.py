# 07 号 NPU 图标颜色规则测试（2026-09-30 用户拍板：三态状态机 → 二色纯函数）：
#   pytest openpilot/selfdrive/ui/tests/test_npu_state.py
# 口径：
#   - 「远程大模型」关：不显示（home 判断，不进本函数）。
#   - 开 + onroad + 已连上（BigmodelLinkState ∈ connected/blip/restart）：绿。
#   - 开 + 其余一切（offroad、connecting、lost、""）：橙。
from openpilot.selfdrive.ui.sunnypilot.npu_state import npu_color


def test_onroad_connected_is_green():
  for link in ("connected", "blip", "restart"):
    assert npu_color(True, link) == "green", f"link={link}"


def test_onroad_not_connected_is_orange():
  for link in ("", "connecting", "lost", "bogus"):
    assert npu_color(True, link) == "orange", f"link={link}"


def test_offroad_is_orange_even_with_stale_connected():
  # BigmodelLinkState 是 CLEAR_ON_MANAGER_START、offroad 保留上趟旧值——必须按 started 门控
  for link in ("connected", "blip", "restart", "lost", ""):
    assert npu_color(False, link) == "orange", f"link={link}"


def test_big_frame_window_needs_full_window_and_95_percent():
  from openpilot.selfdrive.ui.sunnypilot.npu_state import BigFrameWindow
  w = BigFrameWindow()
  for _ in range(49):
    w.push(True)
  assert not w.mostly_big()  # 不足 50 帧不显示
  w.push(True)
  assert w.mostly_big()
  w.push(False)
  w.push(False)  # 窗口内 48/50 = 96%
  assert w.mostly_big()
  w.push(False)  # 47/50 = 94%
  assert not w.mostly_big()
  w.clear()
  assert not w.mostly_big()
