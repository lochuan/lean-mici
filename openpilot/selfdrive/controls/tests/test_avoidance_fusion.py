"""车道内偏移融合链测试：收帧计时 → 观测流接收门 → enabled → 贴线钳制 + 闭环叠加/回退。

门的裁决在 plan_offset_or_none（组合链），闭环与贴线钳制在 fuse_curvature（纯函数）。
新鲜度定义由 common/stream_gate 唯一提供。

车体系 y 左正；modelV2 相机系 y 右正（桩按相机系写）。默认自车半宽 0.9m、贴线余量 0.15m，
故 3.5m 车道（半宽 1.75）的贴线上限 = 1.75 - 0.9 - 0.15 = 0.70m。
"""
import math

import pytest

from openpilot.selfdrive.controls.controlsd import fuse_curvature, plan_offset_or_none
from openpilot.selfdrive.eagled import constants as C

NOW = 100.0
RECV = 99.5  # 0.5s 前收帧，在 1s 阈值内
MODEL_CURVATURE = 0.01
PREVIEW = C.L_LOOKAHEAD


class _Line:
  def __init__(self, y, x=(0.0, 20.0, 40.0, 80.0)):
    self.x = [float(v) + C.CAMERA_TO_FRONT for v in x]
    self.y = [float(y)] * len(self.x)


class _Model:
  """直车道模型桩：本道半宽 half_width，模型路径在车道中心偏 path_left 米（左正）。"""

  def __init__(self, half_width=1.75, path_left=0.0, probs=(0.9, 0.9), stds=(0.1, 0.1)):
    self.laneLines = [_Line(-half_width), _Line(-half_width), _Line(half_width), _Line(half_width)]
    self.laneLineProbs = [0.5, probs[0], probs[1], 0.5]
    self.laneLineStds = [0.5, stds[0], stds[1], 0.5]
    self.position = _Line(-path_left)
    self.position.yStd = [0.1] * len(self.position.x)
    self.action = type("A", (), {"desiredCurvature": MODEL_CURVATURE})()


def _implied_offset(curvature):
  """修正曲率 → 它推动路径在预瞄处移动的横向距离（路径在车道中心时 = 钳制后的目标）。"""
  return (curvature - MODEL_CURVATURE) * PREVIEW ** 2 / 2.0


def _fuse(model, offset):
  return fuse_curvature(model, offset, camera_to_front=C.CAMERA_TO_FRONT)


# --- 贴线钳制：第一道安全检查 -------------------------------------------------------

@pytest.mark.parametrize("half_width, cap", [(1.75, 0.70), (2.0, 0.95), (1.5, 0.45), (1.05, 0.0), (0.8, 0.0)])
@pytest.mark.parametrize("sign", [-1.0, 1.0])
def test_target_is_clamped_to_line_cap(half_width, cap, sign):
  out = _fuse(_Model(half_width), sign * 5.0)
  assert _implied_offset(out) == pytest.approx(sign * cap, abs=1e-6)


def test_cap_follows_the_narrowest_point_between_ego_and_preview():
  model = _Model(1.75)
  pinched = [1.75, 1.2, 1.75, 1.75]  # 车道在 x=20m 处收窄到半宽 1.2m，自车处与预瞄处都是 1.75m
  for line, sign in ((model.laneLines[1], -1.0), (model.laneLines[2], 1.0)):
    line.y = [sign * w for w in pinched]
  assert _implied_offset(_fuse(model, 5.0)) == pytest.approx(1.2 - 0.9 - 0.15, abs=1e-6)


def test_target_within_cap_passes_through():
  assert _implied_offset(_fuse(_Model(1.75), 0.3)) == pytest.approx(0.3)
  assert _implied_offset(_fuse(_Model(1.75), -0.3)) == pytest.approx(-0.3)


@pytest.mark.parametrize("probs, stds", [((0.1, 0.9), (0.1, 0.1)), ((0.9, 0.1), (0.1, 0.1)),
                                         ((0.9, 0.9), (0.9, 0.1)), ((0.9, 0.9), (0.1, 0.9))])
def test_untrusted_lane_line_is_pure_model_curvature(probs, stds):
  assert _fuse(_Model(probs=probs, stds=stds), 0.5) == MODEL_CURVATURE


# --- 闭环 ----------------------------------------------------------------------------

def test_zero_error_equals_model_curvature():
  assert _fuse(_Model(path_left=0.4), 0.4) == pytest.approx(MODEL_CURVATURE)


def test_correction_pushes_path_toward_target():
  left_of_path = _fuse(_Model(path_left=0.0), 0.5)
  right_of_path = _fuse(_Model(path_left=0.5), -0.0)
  assert left_of_path > MODEL_CURVATURE      # 目标在路径左侧 → 左转修正（曲率左正）
  assert right_of_path < MODEL_CURVATURE     # 路径偏左、目标居中 → 右转修正


def test_none_offset_is_pure_model_curvature():
  assert _fuse(_Model(), None) == MODEL_CURVATURE


# --- 组合链：计时 → 接收门 → enabled -------------------------------------------------

def test_chain_passes_offset_when_fresh_valid_enabled():
  assert plan_offset_or_none(0.2, last_recv_s=RECV, now=NOW, valid=True, enabled=True) == 0.2


def test_chain_fresh_at_exact_threshold():
  # 接收门唯一边界语义：age == 1.0s 仍新鲜
  assert plan_offset_or_none(0.2, last_recv_s=NOW - 1.0, now=NOW, valid=True, enabled=True) == 0.2


def test_chain_falls_back_when_stale():
  assert plan_offset_or_none(0.2, last_recv_s=NOW - 1.5, now=NOW, valid=True, enabled=True) is None


def test_chain_falls_back_when_never_received():
  assert plan_offset_or_none(0.2, last_recv_s=-math.inf, now=NOW, valid=True, enabled=True) is None


def test_chain_falls_back_when_plan_invalid():
  assert plan_offset_or_none(0.2, last_recv_s=RECV, now=NOW, valid=False, enabled=True) is None


def test_chain_falls_back_when_avoidance_disabled():
  assert plan_offset_or_none(0.2, last_recv_s=RECV, now=NOW, valid=True, enabled=False) is None
