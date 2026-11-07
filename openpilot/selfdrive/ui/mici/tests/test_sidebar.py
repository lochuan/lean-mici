# c4 右竖条（x 476–536，中线 506）：confidence ball 固定居中、onroad NPU 图标进竖条、mici 不再有书签。
#   pytest openpilot/selfdrive/ui/mici/tests/test_sidebar.py
import inspect
import math
from types import SimpleNamespace

import pyray as rl
import pytest

from openpilot.selfdrive.ui.mici.onroad import confidence_ball as confidence_ball_module
from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall, ball_center
from openpilot.selfdrive.ui.onroad.augmented_road_view import BORDER_COLORS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_renderer as hud_renderer_module
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_renderer import HudRendererSP, npu_icon_origin
from openpilot.selfdrive.ui.sunnypilot.npu_state import BIG_FRAME_WINDOW, BigFrameWindow
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus

SCREEN = rl.Rectangle(0, 0, 536, 240)
SIDE_PANEL_CENTER_X = 506


def rgba(color: rl.Color) -> tuple[int, int, int, int]:
  return (color.r, color.g, color.b, color.a)


def dot_colors(monkeypatch, status: UIStatus, confidence: float) -> list[tuple[int, int, int, int]]:
  monkeypatch.setattr(ui_state, "status", status)
  ball = ConfidenceBall()
  ball._confidence_filter.x = confidence
  return [rgba(c) for c in ball._dot_colors()]


def set_model_confidence(monkeypatch, confidence: float) -> None:
  probs = SimpleNamespace(brakeDisengageProbs=[1.0 - confidence], steerOverrideProbs=[0.0])
  model = SimpleNamespace(meta=SimpleNamespace(disengagePredictions=probs))
  monkeypatch.setattr(ui_state, "sm", {"modelV2": model})


def test_first_engaged_frame_after_disengaged_is_already_green(monkeypatch):
  set_model_confidence(monkeypatch, 0.9)
  ball = ConfidenceBall()
  monkeypatch.setattr(ui_state, "status", UIStatus.DISENGAGED)
  for _ in range(30):
    ball._update_state()
  monkeypatch.setattr(ui_state, "status", UIStatus.ENGAGED)
  ball._update_state()
  assert [rgba(c) for c in ball._dot_colors()] == [(0, 255, 204, 255), (0, 255, 38, 255)]  # not red/yellow on the way up


def test_confidence_keeps_smoothing_after_the_engage_frame(monkeypatch):
  set_model_confidence(monkeypatch, 0.9)
  ball = ConfidenceBall()
  monkeypatch.setattr(ui_state, "status", UIStatus.ENGAGED)
  ball._update_state()
  set_model_confidence(monkeypatch, 0.0)
  ball._update_state()
  assert 0.0 < ball._confidence_filter.x < 0.9  # moved toward the new target, did not jump


def test_ball_is_fixed_at_side_panel_center():
  center = ball_center(SCREEN)
  assert (center.x, center.y) == (SIDE_PANEL_CENTER_X, 120)


def test_ball_center_follows_rect_offset():
  center = ball_center(rl.Rectangle(100, 30, 536, 240))
  assert (center.x, center.y) == (100 + SIDE_PANEL_CENTER_X, 30 + 120)


def test_engaged_colors_by_confidence_band(monkeypatch):
  green = [(0, 255, 204, 255), (0, 255, 38, 255)]
  yellow = [(255, 200, 0, 255), (255, 115, 0, 255)]
  red = [(255, 0, 21, 255), (255, 0, 89, 255)]
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, 0.8) == green
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, 0.3) == yellow
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, 0.1) == red


def test_confidence_thresholds_are_strictly_greater(monkeypatch):
  green_top, yellow_top, red_top = (0, 255, 204, 255), (255, 200, 0, 255), (255, 0, 21, 255)
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, 0.5)[0] == yellow_top  # 0.5 is not > 0.5
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, math.nextafter(0.5, 1))[0] == green_top
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, 0.2)[0] == red_top  # 0.2 is not > 0.2
  assert dot_colors(monkeypatch, UIStatus.ENGAGED, math.nextafter(0.2, 1))[0] == yellow_top


def test_lat_long_only_use_border_colors(monkeypatch):
  for status in (UIStatus.LAT_ONLY, UIStatus.LONG_ONLY):
    border = rgba(BORDER_COLORS[status])
    assert dot_colors(monkeypatch, status, 0.9) == [border, border]


def test_override_fades_white_to_gray(monkeypatch):
  assert dot_colors(monkeypatch, UIStatus.OVERRIDE, 0.9) == [(255, 255, 255, 255), (82, 82, 82, 255)]


def test_disengaged_draws_dark_gray_ball_at_fixed_center(monkeypatch):
  monkeypatch.setattr(ui_state, "status", UIStatus.DISENGAGED)
  drawn = []
  monkeypatch.setattr(confidence_ball_module, "draw_circle_gradient", lambda *args: drawn.append(args))
  ball = ConfidenceBall()
  ball.set_rect(SCREEN)
  ball._confidence_filter.x = -0.5  # the DISENGAGED target
  ball._render(SCREEN)
  (x, y, radius, top, bottom), = drawn
  assert (x, y, radius) == (SIDE_PANEL_CENTER_X, 120, 24)
  assert (rgba(top), rgba(bottom)) == ((50, 50, 50, 255), (13, 13, 13, 255))


@pytest.mark.parametrize("texture_size", [(43, 31), (43, 32), (54, 40)])
def test_npu_icon_is_centered_in_side_panel_at_cy_214(texture_size):
  width, height = texture_size
  origin = npu_icon_origin(SCREEN, width, height)
  # rounding rule: half pixels floor, so the real center is at most 0.5px left/above the target
  assert (origin.x, origin.y) == (math.floor(SIDE_PANEL_CENTER_X - width / 2), math.floor(214 - height / 2))
  assert 0 <= SIDE_PANEL_CENTER_X - (origin.x + width / 2) <= 0.5
  assert 0 <= 214 - (origin.y + height / 2) <= 0.5


def make_hud_with_big_frames(big_count: int) -> HudRendererSP:
  hud = HudRendererSP.__new__(HudRendererSP)  # skip __init__: it loads textures, which needs a window
  hud._txt_npu_green = SimpleNamespace(width=43, height=31)
  hud._big_frames = BigFrameWindow()
  hud._nav_card = SimpleNamespace(render_icon=lambda rect: None, render_card=lambda rect: None, update=lambda: None)
  for i in range(BIG_FRAME_WINDOW):
    hud._big_frames.push(i < big_count)
  return hud


@pytest.mark.parametrize("bigmodel_enabled, big_count, shown", [
  (True, BIG_FRAME_WINDOW, True),
  (False, BIG_FRAME_WINDOW, False),  # toggle off
  (True, 10, False),  # not mostly big
])
def test_npu_icon_gate(monkeypatch, bigmodel_enabled, big_count, shown):
  monkeypatch.setattr(ui_state, "bigmodel_enabled", bigmodel_enabled)
  drawn = []
  monkeypatch.setattr(hud_renderer_module.rl, "draw_texture_ex", lambda *args: drawn.append(args))
  make_hud_with_big_frames(big_count).render_side_panel(SCREEN)
  assert len(drawn) == int(shown)
  if shown:
    position = drawn[0][1]
    assert (position.x, position.y) == (484, 198)


def test_hud_renderer_sp_keeps_blind_spot_hook_used_by_base_class():
  # 基类 _draw_steering_wheel 在 sunnypilot_ui 下会调它（去掉 NPU 让位逻辑时误删过一次，只有真实渲染才暴露）
  assert callable(getattr(HudRendererSP, "_has_blind_spot_detected", None))


def test_augmented_road_view_takes_no_bookmark_callback():
  assert "bookmark_callback" not in inspect.signature(AugmentedRoadView).parameters


def test_side_panel_draws_nav_icon_and_content_area_draws_nav_card():
  hud = make_hud_with_big_frames(0)
  calls = []
  hud._nav_card = SimpleNamespace(render_icon=lambda rect: calls.append(('icon', rect.width)),
                                  render_card=lambda rect: calls.append(('card', rect.width)))
  hud.render_side_panel(SCREEN)
  hud.render_nav_card(rl.Rectangle(0, 0, 476, 240))
  assert calls == [('icon', 536), ('card', 476)]
