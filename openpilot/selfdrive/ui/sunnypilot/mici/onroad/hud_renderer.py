"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.npu_state import BigFrameWindow
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self._big_frames = BigFrameWindow()
    self._txt_npu_green: rl.Texture = gui_app.texture('icons_mici/NPU_GREEN.png', 54, 40)

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()
    if not ui_state.started:
      self._big_frames.clear()
    elif ui_state.sm.updated['modelV2']:
      self._big_frames.push(ui_state.sm['modelV2'].big)

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self.blind_spot_indicators.render(rect)
    # 左上角与 set speed 同位，set speed 显示的那 2.5 s 让位
    if ui_state.bigmodel_enabled and self._big_frames.mostly_big() and not self.drawing_top_icons():
      rl.draw_texture_ex(self._txt_npu_green, rl.Vector2(rect.x + 20, rect.y + 14), 0.0, 1.0, rl.WHITE)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected
