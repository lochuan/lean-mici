"""避让偏置融合链测试：收帧计时 → 观测流接收门 → enabled → 叠加/回退。

2026-09-25 路测后语义：curvatureBias 是纯偏置分量，叠加到**当拍**模型曲率上；
任一门不通回退纯模型曲率。门的裁决在 plan_bias_or_none（组合链），
叠加在 fuse_curvature（纯叠加）。fix/stream-gate 后新鲜度定义由
common/stream_gate 唯一提供。
"""
import math

import pytest

from openpilot.selfdrive.controls.controlsd import fuse_curvature, plan_bias_or_none
from openpilot.selfdrive.eagled.avoidance_planner import plan

NOW = 100.0
RECV = 99.5  # 0.5s 前收帧，在 1s 阈值内


def test_fusion_adds_bias_to_current_model_curvature():
  """只叠加偏置,模型曲率永远取 controlsd 当拍的。

  2026-09-25 路测实测:整句替换（旧语义）把 eagled 拍的模型曲率（p50 188ms
  陈旧）带进转向,偏差最大 1.46e-3 ≈ 0.9m 等效偏移,超过 0.35m 避让上限。
  """
  assert fuse_curvature(model=0.01, bias=0.002) == pytest.approx(0.012)


def test_fusion_with_none_bias_is_pure_model_curvature():
  assert fuse_curvature(model=0.01, bias=None) == 0.01


# --- 组合链：计时 → 接收门 → enabled（fix/stream-gate 的接线断言） -----------------

def test_chain_passes_bias_when_fresh_valid_enabled():
  assert plan_bias_or_none(0.002, last_recv_s=RECV, now=NOW, valid=True, enabled=True) == 0.002


def test_chain_fresh_at_exact_threshold():
  # 接收门唯一边界语义：age == 1.0s 仍新鲜
  assert plan_bias_or_none(0.002, last_recv_s=NOW - 1.0, now=NOW, valid=True, enabled=True) == 0.002


def test_chain_falls_back_when_stale():
  assert plan_bias_or_none(0.002, last_recv_s=NOW - 1.5, now=NOW, valid=True, enabled=True) is None


def test_chain_falls_back_when_never_received():
  assert plan_bias_or_none(0.002, last_recv_s=-math.inf, now=NOW, valid=True, enabled=True) is None


def test_chain_falls_back_when_plan_invalid():
  assert plan_bias_or_none(0.002, last_recv_s=RECV, now=NOW, valid=False, enabled=True) is None


def test_chain_falls_back_when_avoidance_disabled():
  assert plan_bias_or_none(0.002, last_recv_s=RECV, now=NOW, valid=True, enabled=False) is None


def test_plan_requires_explicit_targets():
  with pytest.raises(TypeError):
    plan()  # 不允许缺省幽灵目标
