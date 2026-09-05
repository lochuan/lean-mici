"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.cereal import log
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL

from openpilot.sunnypilot.selfdrive.controls.lib.model_bias_source import ModelBiasSource, EXTRA_OFFSET_LIMIT
from openpilot.sunnypilot.selfdrive.controls.lib.radar_source import RadarSource

LaneChangeState = log.LaneChangeState

# geometry / actuation
HALF_WIDTH = 1.0          # m, ego half width incl. mirrors
T_LOOKAHEAD = 2.0         # s, pure pursuit lookahead time
L_MIN = 10.0              # m
K_NUDGE_LIMIT = 0.005     # 1/m, defensive clamp on the output curvature
MIN_SPEED = 5.0           # m/s
LANE_WIDTH_MIN = 2.7      # m
LANE_WIDTH_MAX = 4.6      # m
LANE_LINE_PROB_MIN = 0.5

# hysteresis on demand strength (0..1)
DEMAND_ENTER = 0.6
DEMAND_EXIT = 0.3
ENTER_TIME = 0.5          # s
EXIT_TIME = 2.0           # s

# smoothing envelope
RAMP_UP_TIME = 1.5        # s
RAMP_DOWN_TIME = 2.5      # s

SOURCE_ENUMS = {'model': 0, 'radar': 1, 'vision': 2}


def smoothstep5(x: float) -> float:
  x = float(np.clip(x, 0.0, 1.0))
  return 10 * x**3 - 15 * x**4 + 6 * x**5


class LateralAvoidancePlanner:
  def __init__(self):
    self.params = Params()
    self.enabled = False
    self.max_offset = 0.40        # m
    self.line_margin = 0.30       # m
    self.vru_margin = 1.00        # m
    self.vru_enabled = True
    self.truck_enabled = True
    self.model_bias_enabled = True
    self.param_read_counter = 0

    self.model_bias_source = ModelBiasSource()
    self.radar_source = RadarSource()

    self.k_nudge = 0.0
    self.y_target = 0.0
    self.y_current = 0.0
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.active = False
    self.objects: list[dict] = []

    self._envelope = 0.0
    self._engaged = False
    self._enter_timer = 0.0
    self._exit_timer = 0.0

  def read_params(self) -> None:
    self.enabled = self.params.get_bool("LateralAvoidanceEnabled")
    self.max_offset = float(self.params.get("LateralAvoidanceMaxOffset", return_default=True))
    self.line_margin = float(self.params.get("LateralAvoidanceLineMargin", return_default=True))
    self.vru_margin = float(self.params.get("LateralAvoidanceVRUMargin", return_default=True))
    self.vru_enabled = self.params.get_bool("LateralAvoidanceVRUEnabled")
    self.truck_enabled = self.params.get_bool("LateralAvoidanceTruckEnabled")
    self.model_bias_enabled = self.params.get_bool("LateralAvoidanceModelBiasEnabled")

  def update_params(self) -> None:
    if self.param_read_counter % 50 == 0:
      self.read_params()
    self.param_read_counter += 1

  def reset(self) -> None:
    self.k_nudge = 0.0
    self.y_target = 0.0
    self.y_current = 0.0
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.active = False
    self.objects = []
    self._envelope = 0.0
    self._engaged = False
    self._enter_timer = 0.0
    self._exit_timer = 0.0
    self.model_bias_source.reset()
    self.radar_source.reset()

  def _decay(self) -> None:
    self._envelope = max(0.0, self._envelope - DT_CTRL / RAMP_DOWN_TIME)
    self._engaged = False
    self._enter_timer = 0.0
    self._exit_timer = 0.0
    # k_nudge drops to zero immediately on gate-out (conservative); the envelope only drives state/telemetry
    self.k_nudge = 0.0
    self.y_target = 0.0
    self.demand_left = 0.0
    self.demand_right = 0.0
    self.active = self._envelope > 0.0

  def update(self, model_v2, radar_points, v_ego: float, lat_active: bool,
             left_blinker: bool, right_blinker: bool, left_blindspot: bool, right_blindspot: bool) -> None:
    self.update_params()

    if not lat_active or not self.enabled:
      self.reset()
      return

    lane_lines = model_v2.laneLines
    lane_line_probs = list(model_v2.laneLineProbs)
    d_left = float(lane_lines[1].y[0])    # > 0
    d_right = -float(lane_lines[2].y[0])  # > 0
    y_ego = (d_right - d_left) / 2.0      # ego offset from lane center, + = left of center
    self.y_current = y_ego

    desire_state = model_v2.meta.desireState
    desire_ok = len(desire_state) == 0 or int(np.argmax(desire_state)) == 0

    gates_ok = (lane_line_probs[1] > LANE_LINE_PROB_MIN and lane_line_probs[2] > LANE_LINE_PROB_MIN
                and LANE_WIDTH_MIN <= d_left + d_right <= LANE_WIDTH_MAX
                and v_ego >= MIN_SPEED
                and model_v2.meta.laneChangeState == LaneChangeState.off
                and not left_blinker and not right_blinker
                and desire_ok)

    if not gates_ok:
      self._decay()
      return

    self.model_bias_source.update(model_v2.position, lane_lines, lane_line_probs,
                                  float(model_v2.action.desiredCurvature), v_ego)
    self.radar_source.update(radar_points, d_left, d_right, self.truck_enabled)

    bias = self.model_bias_source.extra_offset if self.model_bias_enabled else 0.0
    bias_demand = abs(bias) / EXTRA_OFFSET_LIMIT
    radar_dl = self.radar_source.demand_left if self.truck_enabled else 0.0
    radar_dr = self.radar_source.demand_right if self.truck_enabled else 0.0

    demand_left = max(bias_demand if bias > 0.0 else 0.0, radar_dl)
    demand_right = max(bias_demand if bias < 0.0 else 0.0, radar_dr)

    if left_blindspot:
      demand_left = 0.0
    if right_blindspot:
      demand_right = 0.0

    self.demand_left = demand_left
    self.demand_right = demand_right

    req_left = max(bias if bias > 0.0 else 0.0, radar_dl * self.max_offset)
    req_right = max(-bias if bias < 0.0 else 0.0, radar_dr * self.max_offset)
    net_offset = req_left - req_right
    if left_blindspot and net_offset > 0.0:
      net_offset = 0.0
    if right_blindspot and net_offset < 0.0:
      net_offset = 0.0

    strength = max(demand_left, demand_right)
    if strength > DEMAND_ENTER:
      self._enter_timer += DT_CTRL
      self._exit_timer = 0.0
      if self._enter_timer >= ENTER_TIME:
        self._engaged = True
    elif strength < DEMAND_EXIT:
      self._exit_timer += DT_CTRL
      self._enter_timer = 0.0
      if self._exit_timer >= EXIT_TIME:
        self._engaged = False
    else:
      self._enter_timer = 0.0
      self._exit_timer = 0.0

    if self._engaged:
      self._envelope = min(1.0, self._envelope + DT_CTRL / RAMP_UP_TIME)
    else:
      self._envelope = max(0.0, self._envelope - DT_CTRL / RAMP_DOWN_TIME)

    y_target = net_offset * smoothstep5(self._envelope)

    max_left = d_left - HALF_WIDTH - self.line_margin
    max_right = d_right - HALF_WIDTH - self.line_margin
    y_target = float(np.clip(y_target, -max_right, max_left))
    self.y_target = y_target

    self.active = self._envelope > 0.0
    self.objects = self.radar_source.objects

    if self.active:
      lookahead = max(v_ego * T_LOOKAHEAD, L_MIN)
      k_nudge = 2.0 * (y_target - y_ego) / (lookahead * lookahead)
      self.k_nudge = float(np.clip(k_nudge, -K_NUDGE_LIMIT, K_NUDGE_LIMIT))
    else:
      self.k_nudge = 0.0


def fill_lateral_avoidance_msg(la_msg, planner: LateralAvoidancePlanner) -> None:
  la_msg.active = planner.active
  la_msg.yTarget = planner.y_target
  la_msg.yCurrent = planner.y_current
  la_msg.demandLeft = planner.demand_left
  la_msg.demandRight = planner.demand_right
  la_msg.modelBiasExtra = planner.model_bias_source.extra_offset
  objs = la_msg.init('objects', len(planner.objects))
  for i, obj in enumerate(planner.objects):
    o = objs[i]
    o.source = SOURCE_ENUMS.get(obj['source'], 0)
    o.classId = obj['classId']
    o.x = obj['x']
    o.y = obj['y']
    o.demand = obj['demand']
    o.score = obj['score']
