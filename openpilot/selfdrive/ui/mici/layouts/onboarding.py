import math
import numpy as np
import pyray as rl
from collections.abc import Callable
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.qrcode import make_texture
from openpilot.system.ui.lib.application import FontWeight, gui_app, TextAlignment
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import SmallCircleIconButton
from openpilot.system.ui.widgets.scroller import NavScroller, Scroller
from openpilot.system.ui.widgets.nav_widget import NavWidget
from openpilot.system.ui.mici_setup import GreyBigButton, BigPillButton
from openpilot.system.ui.widgets.label import gui_label
from openpilot.system.ui.lib.multilang import tr
from openpilot.common.version import terms_version, training_version, terms_version_sp
from openpilot.selfdrive.ui.ui_state import ui_state, device
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationCircleButton


class TrainingGuideAttentionNotice(Scroller):
  def __init__(self, continue_callback: Callable[[], None]):
    super().__init__()

    continue_button = BigPillButton("next")
    continue_button.set_click_callback(continue_callback)

    self._scroller.add_widgets([
      GreyBigButton("what is sunnypilot?", "scroll to continue",
                    gui_app.texture("icons_mici/setup/green_info.png", 64, 64)),
      GreyBigButton("", "1. sunnypilot is a driver assistance system."),
      GreyBigButton("", "2. You must pay attention at all times."),
      GreyBigButton("", "3. You must be ready to take over at any time."),
      GreyBigButton("", "4. You are fully responsible for driving the car."),
      continue_button,
    ])


class TrainingGuide(NavWidget):
  def __init__(self, completed_callback: Callable[[], None]):
    super().__init__()

    self._steps = [
      TrainingGuideAttentionNotice(continue_callback=completed_callback),
    ]

    self._child(self._steps[0])
    self._steps[0].set_enabled(lambda: self.enabled and not self.is_dismissing)  # for nav stack

  def _render(self, _):
    self._steps[0].render(self._rect)


class QRCodeWidget(Widget):
  def __init__(self, url: str, size: int = 170):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, size, size))
    self._size = size
    self._qr_texture = make_texture(url, inverted=True)

  def _render(self, _):
    if self._qr_texture:
      scale = self._size / self._qr_texture.height
      rl.draw_texture_ex(self._qr_texture, rl.Vector2(round(self._rect.x), round(self._rect.y)), 0.0, scale, rl.WHITE)

  def __del__(self):
    if self._qr_texture and self._qr_texture.id != 0:
      rl.unload_texture(self._qr_texture)


class TermsPage(Scroller):
  def __init__(self, on_accept, on_decline):
    super().__init__()

    self._accept_button = BigConfirmationCircleButton("accept\nterms", gui_app.texture("icons_mici/setup/driver_monitoring/dm_check.png", 64, 64), on_accept)
    self._decline_button = BigConfirmationCircleButton("decline &\nuninstall", gui_app.texture("icons_mici/setup/cancel.png", 64, 64), on_decline,
                                                       red=True, exit_on_confirm=False)

    self._terms_header = GreyBigButton("terms of\nservice", "scroll to continue",
                                       gui_app.texture("icons_mici/setup/green_info.png", 64, 64))
    self._must_accept_card = GreyBigButton("", "You must accept the Terms of Service to use sunnypilot.")

    self._scroller.add_widgets([
      self._terms_header,
      GreyBigButton("swipe for QR code", "or go to https://sunnypilot.ai/terms",
                    gui_app.texture("icons_mici/setup/small_slider/slider_arrow.png", 64, 56, flip_x=True)),
      QRCodeWidget("https://sunnypilot.ai/terms"),
      self._must_accept_card,
      self._accept_button,
      self._decline_button,
    ])

  def _render(self, _):
    rl.draw_rectangle_rec(self._rect, rl.BLACK)
    super()._render(_)


class OnboardingWindow(Widget):
  def __init__(self, completed_callback: Callable[[], None]):
    super().__init__()
    self._completed_callback = completed_callback
    self._accepted_terms: bool = (ui_state.params.get("HasAcceptedTerms") == terms_version and
                                  ui_state.params.get("HasAcceptedTermsSP") == terms_version_sp)
    self._training_done: bool = ui_state.params.get("CompletedTrainingVersion") == training_version

    self.set_rect(rl.Rectangle(0, 0, gui_app.width, gui_app.height))

    # Windows — all pushed onto nav stack, _terms is always rendered as base layer
    self._terms = TermsPage(on_accept=self._on_terms_accepted, on_decline=self._on_uninstall)
    self._terms.set_enabled(lambda: self.enabled)  # for nav stack

    self._training_guide = TrainingGuide(completed_callback=self._on_completed_training)
    self._training_guide.set_enabled(lambda: self.enabled)  # for nav stack

    self._needs_initial_push = False

  def _on_uninstall(self):
    ui_state.params.put_bool("DoUninstall", True, block=True)

  def show_event(self):
    super().show_event()
    device.set_override_interactive_timeout(300)
    device.set_offroad_brightness(100)
    self._needs_initial_push = True

  def hide_event(self):
    super().hide_event()
    # FIXME: when nav stack sends hide event to widget 2 below on push, this needs to be moved
    device.set_override_interactive_timeout(None)
    device.set_offroad_brightness(None)

  @property
  def completed(self) -> bool:
    return self._accepted_terms and self._training_done

  def close(self):
    ui_state.params.put_bool("IsDriverViewEnabled", False)
    self._completed_callback()

  def _on_terms_accepted(self):
    ui_state.params.put("HasAcceptedTerms", terms_version, block=True)
    ui_state.params.put("HasAcceptedTermsSP", terms_version_sp, block=True)
    self._accepted_terms = True
    if not self._training_done:
      gui_app.push_widget(self._training_guide)
    else:
      self.close()

  def _on_completed_training(self):
    ui_state.params.put("CompletedTrainingVersion", training_version, block=True)
    self._training_done = True
    self.close()

  def _render(self, _):
    rl.draw_rectangle_rec(self._rect, rl.BLACK)

    # Deferred from show_event to avoid nested push_widget re-enable bug
    if self._needs_initial_push:
      self._needs_initial_push = False
      if self._accepted_terms and not self._training_done:
        gui_app.push_widget(self._training_guide)

    self._terms.render(self._rect)
