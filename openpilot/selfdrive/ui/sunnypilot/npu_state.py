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

# C4 端 UI 一律 ASCII 文案（中文字体缺字形渲染成问号，用户拍板 2026-09-30）
PROMPT_REASONS = {
  "blip": "Network blip, recovering",
  "restart": "Server restarted, recovering",
  "lost": "Connection lost, reconnecting",
  "connecting": "Reconnecting",
  "connected": "Timed out, using small model",
}


class NpuState(Enum):
  CONNECTING = "connecting"
  ACTIVE = "active"
  FALLBACK = "fallback"


class NpuIconState:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    """回到「连接中」（onroad/offroad 切换时调）。"""
    self.state = NpuState.CONNECTING
    self._up_streak = 0
    self._down_streak = 0
    self._prompt_due: float | None = None
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
        self._prompt_due = now + PROMPT_AFTER_S  # 进橙武装一次提示
    else:  # FALLBACK
      if self._up_streak >= HYSTERESIS_FRAMES:
        self.state = NpuState.ACTIVE
        if self._prompt_due is not None and now < self._prompt_due:
          self._prompt_due = None  # 橙没撑到 10 s，不该提示

  def prompt_text(self, now: float) -> str | None:
    """进橙 10 s 后返回提示文案第二行，撑满 PROMPT_DURATION_S（到点即武装：
    到点后回绿不打断显示）；窗口外 None。每次进橙至多一次。"""
    if self._prompt_due is None or now < self._prompt_due:
      return None
    if now < self._prompt_due + PROMPT_DURATION_S:
      return PROMPT_REASONS.get(self._link_state, PROMPT_REASONS["connecting"])
    return None


# UI 进程全局单例（ui_state 喂帧，home/alert_renderer 读状态）
npu_icon_state = NpuIconState()
