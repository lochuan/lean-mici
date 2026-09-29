"""07 号：NPU 图标显示状态机（纯逻辑，无 UI 依赖）。

三态：灰 CONNECTING（连接中）/ 绿 ACTIVE（大模型控车中）/ 橙 FALLBACK（回退/错误）。
口径（票面 + 用户拍板）：
  - 进橙 N=3 帧连续不可用，回绿 M=3 帧连续可用；孤立单帧超时图标不动。
  - 从未连上全静默：灰态没有通往橙色的转移，不弹提示（没连上大模型服务就是小模型开车）。
  - 只有曾绿后回退才进橙；橙持续 ≥10 s 弹一次提示（时间窗函数，每次进橙至多一次），
    文案按 06 号链路原因区分（blip=网络闪断 / restart=服务端重启 / lost=连接丢失）。
驱动方：ui_state 每收到一条 modelV2 调一次 on_frame（available = modelV2.big）。
"""
from enum import Enum

HYSTERESIS_FRAMES = 3      # 进橙 N = 回绿 M = 3
PROMPT_AFTER_S = 10.0      # 橙持续多久弹提示
PROMPT_DURATION_S = 5.0    # 提示显示时长

PROMPT_REASONS = {
  "blip": "网络闪断，恢复中",
  "restart": "服务端重启，恢复中",
  "lost": "连接丢失，重连中",
  "connecting": "正在重连",
  "connected": "结果超时，已切小模型",
}


class NpuState(Enum):
  CONNECTING = "connecting"
  ACTIVE = "active"
  FALLBACK = "fallback"


class NpuIconState:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    """回到「连接中」（onroad 起新会话时调）。"""
    self.state = NpuState.CONNECTING
    self._up_streak = 0
    self._down_streak = 0
    self._orange_since = 0.0
    self._link_state = ""

  def on_frame(self, available: bool, link_state: str, now: float) -> None:
    self._link_state = link_state or ""
    if available:
      self._up_streak += 1
      self._down_streak = 0
    else:
      self._down_streak += 1
      self._up_streak = 0

    if self.state is NpuState.CONNECTING:
      if self._up_streak >= HYSTERESIS_FRAMES:
        self.state = NpuState.ACTIVE
    elif self.state is NpuState.ACTIVE:
      if self._down_streak >= HYSTERESIS_FRAMES:
        self.state = NpuState.FALLBACK
        self._orange_since = now
    else:  # FALLBACK
      if self._up_streak >= HYSTERESIS_FRAMES:
        self.state = NpuState.ACTIVE

  def prompt_text(self, now: float) -> str | None:
    """橙持续 [10 s, 15 s) 时返回提示文案第二行；窗口外 None（纯时间窗，每次进橙至多一次）。"""
    if self.state is not NpuState.FALLBACK:
      return None
    dt = now - self._orange_since
    if PROMPT_AFTER_S <= dt < PROMPT_AFTER_S + PROMPT_DURATION_S:
      return PROMPT_REASONS.get(self._link_state, PROMPT_REASONS["connecting"])
    return None


# UI 进程全局单例（ui_state 喂帧，home/alert_renderer 读状态）
npu_icon_state = NpuIconState()
