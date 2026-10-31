import math
import pyray as rl
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.lib.application import gui_app
from openpilot.common.filter_simple import FirstOrderFilter

from openpilot.selfdrive.ui.sunnypilot.mici.onroad.confidence_ball import ConfidenceBallSP


def draw_circle_gradient(center_x: float, center_y: float, radius: int,
                         top: rl.Color, bottom: rl.Color) -> None:
  # Draw a square with the gradient
  rl.draw_rectangle_gradient_v(int(center_x - radius), int(center_y - radius),
                               radius * 2, radius * 2,
                               top, bottom)

  # Paint over square with a ring
  outer_radius = math.ceil(radius * math.sqrt(2)) + 1
  rl.draw_ring(rl.Vector2(int(center_x), int(center_y)), radius, outer_radius,
               0.0, 360.0,
               20, rl.BLACK)


def ball_center(rect: rl.Rectangle) -> rl.Vector2:
  """The ball is fixed at the middle of the right side panel."""
  return rl.Vector2(rect.x + rect.width - SIDE_PANEL_WIDTH / 2, rect.y + rect.height / 2)


class ConfidenceBall(Widget, ConfidenceBallSP):
  def __init__(self, demo: bool = False):
    Widget.__init__(self)
    ConfidenceBallSP.__init__(self)
    self._demo = demo
    self._confidence_filter = FirstOrderFilter(-0.5, 0.5, 1 / gui_app.target_fps, initialized=False)

  def update_filter(self, value: float):
    self._confidence_filter.update(value)

  def _update_state(self):
    if self._demo:
      return

    # disengaged ball is always dark gray, so its confidence is unused; the next update snaps to the live value (no red/yellow sweep)
    if ui_state.status == UIStatus.DISENGAGED:
      self._confidence_filter.initialized = False
    elif ui_state.status in (UIStatus.LAT_ONLY, UIStatus.LONG_ONLY):
      self._confidence_filter.update(1 - max(self.get_animate_status_probs() or [1]))
    else:
      self._confidence_filter.update((1 - max(ui_state.sm['modelV2'].meta.disengagePredictions.brakeDisengageProbs or [1])) *
                                                        (1 - max(ui_state.sm['modelV2'].meta.disengagePredictions.steerOverrideProbs or [1])))

  def _dot_colors(self) -> tuple[rl.Color, rl.Color]:
    # confidence zones
    if ui_state.status == UIStatus.ENGAGED or self._demo:
      if self._confidence_filter.x > 0.5:
        return rl.Color(0, 255, 204, 255), rl.Color(0, 255, 38, 255)
      if self._confidence_filter.x > 0.2:
        return rl.Color(255, 200, 0, 255), rl.Color(255, 115, 0, 255)
      return rl.Color(255, 0, 21, 255), rl.Color(255, 0, 89, 255)

    if ui_state.status in (UIStatus.LAT_ONLY, UIStatus.LONG_ONLY):
      color = self.get_lat_long_dot_color()
      return color, color

    if ui_state.status == UIStatus.OVERRIDE:
      return rl.Color(255, 255, 255, 255), rl.Color(82, 82, 82, 255)

    return rl.Color(50, 50, 50, 255), rl.Color(13, 13, 13, 255)

  def _render(self, _):
    center = ball_center(self.rect)
    top_dot_color, bottom_dot_color = self._dot_colors()
    draw_circle_gradient(center.x, center.y, 24, top_dot_color, bottom_dot_color)
