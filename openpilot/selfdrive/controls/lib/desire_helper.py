from openpilot.cereal import log, custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import AutoLaneChangeController, AutoLaneChangeMode
from openpilot.sunnypilot.selfdrive.controls.lib.lane_turn_desire import LaneTurnController

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
TurnDirection = custom.ModelDataV2SP.TurnDirection
Block = custom.ModelDataV2SP.LaneChangeBlock
Hold = custom.ModelDataV2SP.LaneChangeHoldReason

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5
# C9+ 变道清空门:清空标志为 False -> 拦截启动。清空判定在 eagled
# （change_clear）:只看车道线确认位于目标车道内的目标,近区硬拦、速度未知
# （视觉独有目标）不放宽、"远而快"的车按 carrotpilot 4s/3s 投影放行。
# eagleState 缺失/过期、或本车道线不可信（清空状态 unknown）时标志为 None,
# 完全不参与门控（回退纯 BSM 布尔 + relc 边缘）,绝不因感知缺失锁死变道。

TURN_DESIRES = {
  TurnDirection.none: log.Desire.none,
  TurnDirection.turnLeft: log.Desire.turnLeft,
  TurnDirection.turnRight: log.Desire.turnRight,
}

def geometry_block(blindspot, edge_detected, not_clear):
  # 优先级 blindspot > roadEdge > targetNotClear
  if blindspot:
    return Block.blindspot
  if edge_detected:
    return Block.roadEdge
  return Block.targetNotClear if not_clear else Block.none


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none
    self.alc = AutoLaneChangeController(self)
    self.lane_turn_controller = LaneTurnController(self)
    self.lane_turn_direction = TurnDirection.none
    self.block_left = Block.none
    self.block_right = Block.none
    self.hold_reason = Hold.none

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  def update(self, carstate, lateral_active, lane_change_prob, left_edge_detected=False, right_edge_detected=False,
             change_clear_left=None, change_clear_right=None):
    self.alc.update_params()
    self.lane_turn_controller.update_params()
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    # 每侧几何拦截与是否打灯无关：没打灯时 HUD 也要显示左右能否变道。
    self.block_left = geometry_block(carstate.leftBlindspot, left_edge_detected,
                                     change_clear_left is not None and not change_clear_left)
    self.block_right = geometry_block(carstate.rightBlindspot, right_edge_detected,
                                      change_clear_right is not None and not change_clear_right)

    # Lane turn controller update
    self.lane_turn_controller.update_lane_turn(blindspot_left=carstate.leftBlindspot, blindspot_right=carstate.rightBlindspot,
                                               left_blinker=carstate.leftBlinker, right_blinker=carstate.rightBlinker, v_ego=v_ego)
    self.lane_turn_direction = self.lane_turn_controller.get_turn_direction()

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX or self.alc.lane_change_set_timer == AutoLaneChangeMode.OFF:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
    else:
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        torque_applied = carstate.steeringPressed and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        # C9+ 变道清空门:目标道侧不清空即拦启动。三路否决各管一段:
        # BSM 布尔兜底(eagleState 缺失时唯一门)、relc 边缘(目标道不存在,
        # 清空判定不含路沿)、清空标志(eagled 的时间投影:近区/速度未知拦,
        # 远而快的侧车放行)。仅拦启动,starting 之后不复查(与上游一致)。
        blindspot_detected = ((self.block_left != Block.none and self.lane_change_direction == LaneChangeDirection.left) or
                              (self.block_right != Block.none and self.lane_change_direction == LaneChangeDirection.right))

        self.alc.update_lane_change(blindspot_detected, carstate.brakePressed)

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif (torque_applied or self.alc.auto_lane_change_allowed) and not blindspot_detected:
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        if lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    self.prev_one_blinker = one_blinker and lateral_active

    if self.lane_turn_direction != TurnDirection.none:
      self.desire = TURN_DESIRES[self.lane_turn_direction]
    else:
      self.desire = log.Desire.none
      if self.lane_change_state == LaneChangeState.laneChangeStarting:
        if self.lane_change_direction == LaneChangeDirection.left:
          self.desire = log.Desire.laneChangeLeft
        elif self.lane_change_direction == LaneChangeDirection.right:
          self.desire = log.Desire.laneChangeRight

    self.alc.update_state()
    self.hold_reason = self._hold_reason(one_blinker, lateral_active, below_lane_change_speed)

  def _hold_reason(self, one_blinker, lateral_active, below_lane_change_speed):
    # 请求保持原因：仅打灯请求期间有意义；几何拦截已能解释的不重复发布
    if not (lateral_active and one_blinker):
      return Hold.none
    if self.alc.lane_change_set_timer == AutoLaneChangeMode.OFF:
      return Hold.alcOff
    if below_lane_change_speed:
      return Hold.belowSpeed
    if self.lane_change_state != LaneChangeState.preLaneChange:
      return Hold.none
    blocked = self.block_left if self.lane_change_direction == LaneChangeDirection.left else self.block_right
    if blocked != Block.none:
      return Hold.none
    if self.alc.lane_change_set_timer != AutoLaneChangeMode.NUDGE and self.alc.prev_brake_pressed:
      return Hold.brake
    return Hold.awaitingConfirm
