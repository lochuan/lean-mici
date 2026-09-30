# 07 号 NPU 图标显示状态机回放测试：
#   pytest openpilot/selfdrive/ui/tests/test_npu_state.py
# 口径（票面 + 用户拍板）：
#   - 三态：灰=连接中、绿=大模型控车中、橙=回退/错误；进橙 N=3、回绿 M=3，孤立单帧不动。
#   - 从未连上全静默：灰态没有通往橙色的转移，不弹提示（小模型即默认）。
#   - 只有曾绿后回退才进橙；橙持续 ≥10 s 弹一次提示，每次进橙至多一次。
from openpilot.selfdrive.ui.sunnypilot.npu_state import (
  HYSTERESIS_FRAMES, PROMPT_AFTER_S, PROMPT_DURATION_S, PROMPT_REASONS, NpuIconState, NpuState,
)

DT = 0.05  # 20 Hz 模型帧


def replay(state: NpuIconState, frames) -> NpuIconState:
  """回放 (available, link_state, t) 序列。"""
  for available, link, t in frames:
    state.on_frame(available, link, t)
  return state


def frames(available: bool, link: str, t0: float, n: int):
  return [(available, link, t0 + i * DT) for i in range(n)]


def test_boot_connecting_until_three_available():
  st = replay(NpuIconState(), frames(True, "connected", 0.0, HYSTERESIS_FRAMES - 1))
  assert st.state is NpuState.CONNECTING
  st = replay(st, frames(True, "connected", 1.0, 1))
  assert st.state is NpuState.ACTIVE


def test_never_connected_is_silent():
  # 从未连上：百帧不可用 + 各种链路状态都停在灰，不弹任何提示
  for link in ("", "connecting", "lost"):
    st = replay(NpuIconState(), frames(False, link, 0.0, 100))
    assert st.state is NpuState.CONNECTING
    assert all(st.prompt_text(t * DT) is None for t in range(100))


def test_single_frame_timeout_does_not_move():
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5))
  assert st.state is NpuState.ACTIVE
  st = replay(st, frames(False, "connected", 1.0, HYSTERESIS_FRAMES - 1) + frames(True, "connected", 2.0, 3))
  assert st.state is NpuState.ACTIVE


def test_three_unavailable_enter_fallback():
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5))
  st = replay(st, frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
  assert st.state is NpuState.FALLBACK


def test_three_available_exit_fallback():
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5))
  st = replay(st, frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
  st = replay(st, frames(True, "connected", 2.0, HYSTERESIS_FRAMES - 1))
  assert st.state is NpuState.FALLBACK
  st = replay(st, frames(True, "connected", 3.0, 1))
  assert st.state is NpuState.ACTIVE


def test_prompt_window_ten_seconds_once_per_entry():
  t_enter = 1.0 + (HYSTERESIS_FRAMES - 1) * DT  # 第 3 帧不可用进橙的时刻
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5) + frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
  assert st.state is NpuState.FALLBACK
  assert st.prompt_text(t_enter + PROMPT_AFTER_S - 0.1) is None
  assert st.prompt_text(t_enter + PROMPT_AFTER_S) is not None
  assert st.prompt_text(t_enter + PROMPT_AFTER_S + PROMPT_DURATION_S - 0.1) is not None
  assert st.prompt_text(t_enter + PROMPT_AFTER_S + PROMPT_DURATION_S) is None  # 一次窗口，不重复弹
  # 回绿再进橙：新窗口
  st = replay(st, frames(True, "connected", 5.0, HYSTERESIS_FRAMES))
  st = replay(st, frames(False, "connected", 6.0, HYSTERESIS_FRAMES))
  assert st.state is NpuState.FALLBACK
  assert st.prompt_text(6.0 + (HYSTERESIS_FRAMES - 1) * DT + PROMPT_AFTER_S) is not None


def test_prompt_survives_recovery_after_due():
  # 到点即武装：刚过 10 s 就回绿也撑满显示时长，不闪一下就没了
  t_enter = 1.0 + (HYSTERESIS_FRAMES - 1) * DT
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5) + frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
  t_due = t_enter + PROMPT_AFTER_S
  st = replay(st, frames(True, "connected", t_due + 0.1, HYSTERESIS_FRAMES))
  assert st.state is NpuState.ACTIVE
  assert st.prompt_text(t_due + 0.5) is not None
  assert st.prompt_text(t_due + PROMPT_DURATION_S) is None


def test_no_prompt_if_orange_recovered_before_ten_seconds():
  # 橙没撑到 10 s（回绿取消武装），不该弹
  t_enter = 1.0 + (HYSTERESIS_FRAMES - 1) * DT
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5) + frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
  st = replay(st, frames(True, "connected", 2.0, HYSTERESIS_FRAMES))
  assert st.state is NpuState.ACTIVE
  assert st.prompt_text(t_enter + PROMPT_AFTER_S) is None
  assert st.prompt_text(100.0) is None


def test_prompt_reason_texts_follow_link_state():
  # 06 号原因区分：blip/restart/lost 各有文案；文案随 PROMPT_REASONS 走，测试只锁映射
  t_enter = 1.0 + (HYSTERESIS_FRAMES - 1) * DT
  for link in ("blip", "restart", "lost"):
    st = replay(NpuIconState(), frames(True, "connected", 0.0, 5) + frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
    st.on_frame(False, link, t_enter + 5.0)  # 提示时刻前的链路状态决定文案
    text = st.prompt_text(t_enter + PROMPT_AFTER_S)
    assert text == PROMPT_REASONS[link], f"link={link}: {text}"


def test_reset_back_to_connecting():
  st = replay(NpuIconState(), frames(True, "connected", 0.0, 5) + frames(False, "connected", 1.0, HYSTERESIS_FRAMES))
  assert st.state is NpuState.FALLBACK
  st.reset()
  assert st.state is NpuState.CONNECTING
  assert st.prompt_text(100.0) is None
