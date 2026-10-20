"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import math

import pyray as rl

from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.nav_card_renderer import NavCardRenderer
from openpilot.selfdrive.ui.sunnypilot.npu_state import BigFrameWindow
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app

NPU_ICON_FIT_SIZE = (43, 32)  # texture is scaled to fit this box, keeping aspect (actual 43x31)
NPU_ICON_BOTTOM_MARGIN_TO_CENTER = 26  # 与顶部预留的导航图标（cy=26）上下对称


def npu_icon_origin(rect: rl.Rectangle, texture_width: int, texture_height: int) -> rl.Vector2:
  """NPU 图标左上角：图标中心落在右竖条中线、距屏幕底 26px。
  取整规则：半像素向下取整（floor），故奇数尺寸的实际中心最多偏 0.5px。"""
  center_x = rect.x + rect.width - SIDE_PANEL_WIDTH / 2
  center_y = rect.y + rect.height - NPU_ICON_BOTTOM_MARGIN_TO_CENTER
  return rl.Vector2(math.floor(center_x - texture_width / 2), math.floor(center_y - texture_height / 2))


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self._big_frames = BigFrameWindow()
    self._nav_card = NavCardRenderer()
    self._txt_npu_green: rl.Texture = gui_app.texture('icons_mici/NPU_GREEN.png', *NPU_ICON_FIT_SIZE)

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()
    self._nav_card.update()
    if not ui_state.started:
      self._big_frames.clear()
    elif ui_state.sm.updated['modelV2']:
      self._big_frames.push(ui_state.sm['modelV2'].big)

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self.blind_spot_indicators.render(rect)

  def render_side_panel(self, rect: rl.Rectangle) -> None:
    """画在右竖条里（content 的 scissor 之外），rect 为整屏。"""
    self._nav_card.render_icon(rect)
    if ui_state.bigmodel_enabled and self._big_frames.mostly_big():
      tex = self._txt_npu_green
      rl.draw_texture_ex(tex, npu_icon_origin(rect, tex.width, tex.height), 0.0, 1.0, rl.WHITE)

  def render_nav_card(self, content_rect: rl.Rectangle) -> None:
    """导航卡片：画在内容区、HUD 之上；调用方在出警告条时不调用（让位）。"""
    self._nav_card.render_card(content_rect)

  def _has_blind_spot_detected(self) -> bool:
    return self.blind_spot_indicators.detected
