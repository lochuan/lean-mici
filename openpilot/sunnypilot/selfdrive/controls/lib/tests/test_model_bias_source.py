"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.controls.lib.model_bias_source import (
  ModelBiasSource, AMPLIFICATION, EXTRA_OFFSET_LIMIT, BIAS_SIGN_FRAMES, BIAS_MIN_ABS, CURVATURE_MAX,
)

V_EGO = 25.0  # lookahead = clip(25 * 1.5, 8, 30) = 30 m
X_IDXS = ModelConstants.X_IDXS
LOOKAHEAD = float(np.clip(V_EGO * 1.5, 8.0, 30.0))  # 30 m
# model path y at the lookahead (the update_once helper drives the model path at
# +/-0.3 m); the lane lines are fixed at +/-1.8, so the bias equals this value
BIAS_AT_LOOKAHEAD = float(np.interp(LOOKAHEAD, X_IDXS,
                                    [0.3 * min(1.0, x / 30.0) if x > 0 else 0.0 for x in X_IDXS]))


class MockLine:
  def __init__(self, y_val):
    self.x = list(X_IDXS)
    self.y = [y_val] * len(X_IDXS)


class MockPosition:
  def __init__(self, y_at_30):
    self.x = list(X_IDXS)
    self.y = [y_at_30 * min(1.0, x / 30.0) if x > 0 else 0.0 for x in X_IDXS]


def update_once(src, path_y=0.3, ll_left=1.8, ll_right=-1.8, probs=None, curv=0.0):
  probs = probs if probs is not None else [0.9, 0.9, 0.9, 0.9]
  lane_lines = [MockLine(-5.0), MockLine(ll_left), MockLine(ll_right), MockLine(5.0)]
  src.update(MockPosition(path_y), lane_lines, probs, curv, V_EGO)


class TestModelBiasSource(OpenpilotTestCase):
  def test_trusted_bias_amplified(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=0.3)
    assert abs(src.extra_offset - BIAS_AT_LOOKAHEAD * (AMPLIFICATION - 1.0)) < 1e-6

  def test_not_trusted_before_stable(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES - 1):
      update_once(src, path_y=0.3)
    assert src.extra_offset == 0.0

  def test_negative_bias(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=-0.3)
    assert abs(src.extra_offset + BIAS_AT_LOOKAHEAD * (AMPLIFICATION - 1.0)) < 1e-6

  def test_bias_clamped(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=1.0)
    assert src.extra_offset == EXTRA_OFFSET_LIMIT

  def test_low_prob_blocks(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=0.3, probs=[0.9, 0.3, 0.9, 0.9])
    assert src.extra_offset == 0.0

  def test_curvature_gate(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=0.3, curv=CURVATURE_MAX * 2)
    assert src.extra_offset == 0.0

  def test_tiny_bias_ignored(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=BIAS_MIN_ABS * 0.5)
    assert src.extra_offset == 0.0

  def test_sign_flip_resets_trust(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=0.3)
    assert src.extra_offset > 0.0
    for _ in range(BIAS_SIGN_FRAMES - 1):
      update_once(src, path_y=-0.3)
    assert src.extra_offset == 0.0
    update_once(src, path_y=-0.3)
    assert src.extra_offset < 0.0

  def test_reset(self):
    src = ModelBiasSource()
    for _ in range(BIAS_SIGN_FRAMES):
      update_once(src, path_y=0.3)
    src.reset()
    assert src.extra_offset == 0.0 and src.model_bias == 0.0
