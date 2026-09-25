"""观测流接收门：「这条流还新鲜吗」的唯一策略（fix/stream-gate）。

消费方（modeld / controlsd / lanlinkd）每拍经 :func:`stream_status` 判定观测流
是否可用；判定语义（字段组合 + 阈值 + 三态）只在这一处，任何地方不得再手写
``seen && valid && age < N`` 之类的组合或另设阈值。

三态（:class:`StreamStatus`）：

* ``FRESH`` —— 收到了、envelope valid、年龄在阈值内（age == 阈值仍算新鲜）；
* ``STALE`` —— 收到过但超龄，或 envelope invalid；
* ``NEVER_SEEN`` —— 从未收到（age 传 None）。

**回退动作不下沉**：过期后传 None、用当拍值、标 stale 还是别的，是各消费方的
领域知识，留在调用点。本模块只回答「新不新鲜」。

与「健康」相关的三个不同概念，别混用（评审拆词）：

* **消息新鲜度**（本模块）：观测流收帧时限；
* **设备遥测节流**（``eagled/device_health.py`` DeviceHealth）：按 CPU/内存调
  推理节拍，与消息无关；
* **雷达健康字段**（eagleState 的 canError/radarUnavailable）：透传的观察量，
  不参与新鲜度判定。
"""
from __future__ import annotations

from enum import Enum

# 各观测流的接收时限（s）。消费者侧判定超龄即弃用——流名之外不接受自定义阈值，
# 阈值改动只应发生在这一处。
MAX_AGE_S: dict[str, float] = {
  "eagleState": 1.0,
  "lateralManeuverPlan": 1.0,
  "eagleDebug": 1.0,
  "modelV2": 1.0,
}


class StreamStatus(Enum):
  FRESH = "fresh"
  STALE = "stale"
  NEVER_SEEN = "never_seen"


def stream_status(stream: str, age_s: float | None, valid: bool = True) -> StreamStatus:
  """观测流三态判定。``age_s=None`` 表示从未收到；未知流名是编程错误（KeyError）。

  调用方自己持有收帧时刻（SubMaster recv_time 或本地单调钟），这里只做判定。
  """
  if age_s is None:
    return StreamStatus.NEVER_SEEN
  max_age = MAX_AGE_S[stream]  # KeyError = 流名没登记，编程错误
  if valid and age_s <= max_age:
    return StreamStatus.FRESH
  return StreamStatus.STALE
