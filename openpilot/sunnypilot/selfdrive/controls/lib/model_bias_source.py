"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

LOOKAHEAD_TIME = 1.5       # s
LOOKAHEAD_MIN = 8.0        # m
LOOKAHEAD_MAX = 30.0       # m
AMPLIFICATION = 1.3
EXTRA_OFFSET_LIMIT = 0.15  # m
BIAS_SIGN_FRAMES = 5       # consecutive same-sign evaluations to trust the bias
BIAS_MIN_ABS = 0.03        # m, ignore tiny biases
CURVATURE_MAX = 0.005      # 1/m, amplify only on straight-ish roads
LANE_LINE_PROB_MIN = 0.5


class ModelBiasSource:
  def __init__(self):
    self.extra_offset = 0.0
    self.model_bias = 0.0
    self._sign_history: list[int] = []

  def reset(self) -> None:
    self.extra_offset = 0.0
    self.model_bias = 0.0
    self._sign_history.clear()

  def update(self, position, lane_lines, lane_line_probs, desired_curvature: float, v_ego: float) -> None:
    x_la = float(np.clip(v_ego * LOOKAHEAD_TIME, LOOKAHEAD_MIN, LOOKAHEAD_MAX))
    model_path_y = float(np.interp(x_la, position.x, position.y))
    ll_left_y = float(np.interp(x_la, lane_lines[1].x, lane_lines[1].y))
    ll_right_y = float(np.interp(x_la, lane_lines[2].x, lane_lines[2].y))
    self.model_bias = model_path_y - (ll_left_y + ll_right_y) / 2.0

    gates = (lane_line_probs[1] > LANE_LINE_PROB_MIN and lane_line_probs[2] > LANE_LINE_PROB_MIN
             and abs(desired_curvature) < CURVATURE_MAX)

    if gates and abs(self.model_bias) > BIAS_MIN_ABS:
      self._sign_history.append(int(np.sign(self.model_bias)))
    else:
      self._sign_history.append(0)
    if len(self._sign_history) > BIAS_SIGN_FRAMES:
      self._sign_history.pop(0)

    trusted = (len(self._sign_history) == BIAS_SIGN_FRAMES
               and all(s != 0 for s in self._sign_history)
               and len(set(self._sign_history)) == 1)

    if trusted and gates:
      self.extra_offset = float(np.clip(self.model_bias * (AMPLIFICATION - 1.0),
                                        -EXTRA_OFFSET_LIMIT, EXTRA_OFFSET_LIMIT))
    else:
      self.extra_offset = 0.0
