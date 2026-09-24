import pytest

from openpilot.selfdrive.eagled.avoidance_planner import plan
from openpilot.selfdrive.controls.controlsd import fuse_curvature


def test_fusion_adds_bias_to_current_model_curvature():
  """只叠加偏置,模型曲率永远取 controlsd 当拍的。

  2026-09-25 路测实测:整句替换（旧语义）把 eagled 拍的模型曲率（p50 188ms
  陈旧）带进转向,偏差最大 1.46e-3 ≈ 0.9m 等效偏移,超过 0.35m 避让上限。
  """
  assert fuse_curvature(model=0.01, bias=0.002, valid=True, enabled=True) == pytest.approx(0.012)


def test_fallback_when_avoidance_disabled():
  assert fuse_curvature(model=0.01, bias=0.002, valid=True, enabled=False) == 0.01


def test_fallback_when_plan_invalid():
  assert fuse_curvature(model=0.01, bias=0.002, valid=False, enabled=True) == 0.01


def test_stale_plan_falls_back_to_model():
  assert fuse_curvature(model=0.01, bias=0.002, valid=True, enabled=True, fresh=False) == 0.01


def test_plan_requires_explicit_targets():
  with pytest.raises(TypeError):
    plan()  # 不允许缺省幽灵目标
