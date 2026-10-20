# C4 导航：navStateSP → 卡片 / 竖条图标（新鲜度、到达 5 s、让位前提、图标位置）。
#   pytest openpilot/selfdrive/ui/mici/tests/test_nav_card_render.py
from types import SimpleNamespace

import pyray as rl
import pytest

from openpilot.cereal import messaging
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import nav_card_renderer as module
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.nav_card_renderer import NavCardRenderer
from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as road_view_module
from openpilot.selfdrive.ui.sunnypilot.nav_card import Arrow, CardKind, NavIcon, format_distance, format_minutes
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib import application as application_module

SCREEN = rl.Rectangle(0, 0, 536, 240)
CONTENT = rl.Rectangle(0, 0, 476, 240)
NOW = 1000.0


def nav_message(status="navigating", maneuver=15, distance=350, has_nav_state=True, remaining_m=8400, remaining_s=720, exit_index=0):
  msg = messaging.new_message('navStateSP')
  msg.navStateSP.hasNavState = has_nav_state
  nav = msg.navStateSP.navState
  nav.status = status
  nav.maneuverType = maneuver
  nav.distanceToManeuverM = distance
  nav.routeRemainingM = remaining_m
  nav.routeRemainingSeconds = remaining_s
  nav.roundaboutExitIndex = exit_index
  return msg.navStateSP


class FakeSm:
  def __init__(self):
    self.updated = {'navStateSP': False}
    self.logMonoTime = {'navStateSP': 0}
    self.recv_time = {'navStateSP': 0.0}
    self.seen = {'navStateSP': False}
    self._message = None

  def __getitem__(self, key):
    return self._message

  def deliver(self, message, log_mono_ns, recv_time):
    self._message = message
    self.updated['navStateSP'] = True
    self.logMonoTime['navStateSP'] = log_mono_ns
    self.recv_time['navStateSP'] = recv_time
    self.seen['navStateSP'] = True


@pytest.fixture
def env(monkeypatch):
  sm = FakeSm()
  monkeypatch.setattr(ui_state, "sm", sm)
  monkeypatch.setattr(ui_state, "is_metric", True, raising=False)
  monkeypatch.setattr(module.time, "monotonic", lambda: NOW)
  monkeypatch.setattr(module.rl, "measure_text_ex", lambda font, text, size, spacing: SimpleNamespace(x=len(text) * 10.0, y=size))
  renderer = NavCardRenderer.__new__(NavCardRenderer)   # 跳过 __init__：它加载字体，需要窗口
  renderer._bold, renderer._medium = "Inter-Bold", "Inter-Medium"
  renderer._time_width, renderer._remaining_width = 80.0, 100.0
  renderer._init_state()
  return sm, renderer


def feed(env, message, log_mono_s=500.0, age=0.0):
  sm, renderer = env
  sm.deliver(message, int(log_mono_s * 1e9), NOW - age)
  renderer.update()
  sm.updated['navStateSP'] = False


def test_navigating_message_gives_green_icon_and_card(env):
  feed(env, nav_message())
  renderer = env[1]
  assert renderer.icon() == NavIcon.GREEN
  card = renderer.card()
  assert (card.kind, card.arrow, card.distance_text, card.time_text, card.remaining_text) == (CardKind.NAVIGATING, Arrow.LEFT, "350 m", "12 min", "8.4 km")


def test_imperial_follows_ui_state_cache(env, monkeypatch):
  monkeypatch.setattr(ui_state, "is_metric", False)
  feed(env, nav_message(distance=60))
  assert env[1].card().distance_text == "200 ft"


def test_roundabout_exit_index_reaches_card(env):
  feed(env, nav_message(maneuver=26, exit_index=3))
  assert (env[1].card().arrow, env[1].card().exit_index) == (Arrow.ROUNDABOUT, 3)


def test_stale_message_hides_icon_and_card(env):
  feed(env, nav_message(), age=2.01)
  assert env[1].icon() == NavIcon.HIDDEN
  assert env[1].card() is None


def test_message_exactly_two_seconds_old_is_still_fresh(env):
  feed(env, nav_message(), age=2.0)
  assert env[1].icon() == NavIcon.GREEN


def test_never_received_draws_nothing(env):
  assert env[1].icon() == NavIcon.HIDDEN
  assert env[1].card() is None


def test_no_nav_state_hides_everything(env):
  feed(env, nav_message(has_nav_state=False))
  assert env[1].icon() == NavIcon.HIDDEN
  assert env[1].card() is None


@pytest.mark.parametrize("status, icon", [("positionLost", NavIcon.SLASHED), ("fault", NavIcon.SLASHED), ("rerouting", NavIcon.GREEN)])
def test_icon_follows_status(env, status, icon):
  feed(env, nav_message(status=status))
  assert env[1].icon() == icon


def test_arrived_card_shows_for_five_seconds_from_first_arrived_message(env):
  feed(env, nav_message(status="arrived"), log_mono_s=500.0)
  assert env[1].card().kind == CardKind.ARRIVED
  feed(env, nav_message(status="arrived"), log_mono_s=504.9)
  assert env[1].card().kind == CardKind.ARRIVED
  feed(env, nav_message(status="arrived"), log_mono_s=505.0)
  assert env[1].card() is None
  assert env[1].icon() == NavIcon.HIDDEN


def test_arrived_timer_restarts_after_leaving_arrived(env):
  feed(env, nav_message(status="arrived"), log_mono_s=500.0)
  feed(env, nav_message(status="navigating"), log_mono_s=501.0)
  feed(env, nav_message(status="arrived"), log_mono_s=510.0)
  assert env[1].card().kind == CardKind.ARRIVED


def test_recomputes_only_when_a_message_arrives(env, monkeypatch):
  feed(env, nav_message())
  calls = []
  monkeypatch.setattr(module, "card_content", lambda **kw: calls.append(kw))
  for _ in range(5):
    env[1].update()   # updated 标志已清，无新消息
  assert calls == []


def rgb(color):
  return (color.r, color.g, color.b) if hasattr(color, "r") else tuple(color)[:3]


def capture_draws(monkeypatch):
  drawn = {"lines": [], "rounded": [], "text": [], "text_colors": [], "text_fonts": []}
  monkeypatch.setattr(module.rl, "draw_line_ex", lambda a, b, t, c: drawn["lines"].append((a, b)))
  monkeypatch.setattr(module.rl, "draw_circle_v", lambda *args: None)
  monkeypatch.setattr(module.rl, "draw_ring", lambda *args: None)
  monkeypatch.setattr(module.rl, "draw_rectangle_rounded", lambda rect, *args: drawn["rounded"].append(rect))
  def record_text(font, text, pos, size, spacing, color):
    drawn["text"].append(text)
    drawn["text_colors"].append(rgb(color))
    drawn["text_fonts"].append(font)

  monkeypatch.setattr(module.rl, "_orig_draw_text_ex", record_text, raising=False)
  return drawn


def test_never_received_ignores_leftover_recv_time(env):
  sm, renderer = env
  sm.recv_time['navStateSP'] = NOW   # seen 仍为 False：未收到过
  assert renderer.icon() == NavIcon.HIDDEN


def test_nothing_drawn_when_hidden(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  env[1].render_icon(SCREEN)
  env[1].render_card(CONTENT)
  assert not any(drawn.values())


def test_icon_is_centered_in_side_panel_at_cy_26(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message())
  env[1].render_icon(SCREEN)
  xs = [p.x for a, b in drawn["lines"] for p in (a, b)]
  ys = [p.y for a, b in drawn["lines"] for p in (a, b)]
  assert 495 <= min(xs) and max(xs) <= 517
  assert 15 <= min(ys) and max(ys) <= 37
  assert abs((min(xs) + max(xs)) / 2 - 506) <= 2 and abs((min(ys) + max(ys)) / 2 - 26) <= 2


def test_card_geometry_and_eta_text(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message())
  renderer = env[1]
  renderer.render_card(CONTENT)
  background = drawn["rounded"][0]
  assert (background.x + background.width, background.y, background.height) == (460, 16, 82)
  assert drawn["text"] == ["350 m", "12 min", "8.4 km"]


@pytest.mark.parametrize("status, text", [("rerouting", "Rerouting"), ("positionLost", "GPS lost"), ("arrived", "Arrived")])
def test_short_cards_have_no_eta_and_height_50(env, monkeypatch, status, text):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message(status=status))
  env[1].render_card(CONTENT)
  assert drawn["text"] == [text]
  assert drawn["rounded"][0].height == 50


def test_position_lost_draws_red_slash_over_arrow(env, monkeypatch):
  colors = []
  capture_draws(monkeypatch)
  monkeypatch.setattr(module.rl, "draw_line_ex", lambda a, b, t, c: colors.append((c.r, c.g, c.b)))
  feed(env, nav_message(status="positionLost"))
  env[1].render_card(CONTENT)
  assert (210, 90, 90) in colors


def test_unknown_arrow_type_leaves_arrow_slot_empty_but_keeps_text(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message(maneuver=0))
  env[1].render_card(CONTENT)
  assert drawn["lines"] == []
  assert drawn["text"] == ["350 m", "12 min", "8.4 km"]


MUTED_RGB, ACCENT_RGB, RED_RGB, WHITE_RGB = (162, 175, 185), (155, 230, 185), (210, 90, 90), (255, 255, 255)


@pytest.mark.parametrize("status, color", [("rerouting", MUTED_RGB), ("positionLost", RED_RGB), ("arrived", WHITE_RGB)])
def test_short_card_phrase_colors(env, monkeypatch, status, color):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message(status=status))
  env[1].render_card(CONTENT)
  assert drawn["text_colors"] == [color]


def test_navigating_text_colors(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message())
  env[1].render_card(CONTENT)
  assert drawn["text_colors"] == [WHITE_RGB, ACCENT_RGB, MUTED_RGB]   # 距离白、剩余时间强调色、剩余距离灰


def test_roundabout_enter_draws_exit_number_in_ring(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message(maneuver=26, exit_index=3))
  env[1].render_card(CONTENT)
  assert "3" in drawn["text"]


def test_roundabout_exit_and_zero_index_draw_no_number(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message(maneuver=27, exit_index=3))
  env[1].render_card(CONTENT)
  feed(env, nav_message(maneuver=26, exit_index=0))
  env[1].render_card(CONTENT)
  assert "3" not in drawn["text"] and "0" not in drawn["text"]


def test_card_width_follows_measured_worst_case_text(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  sm, renderer = env
  renderer._time_width, renderer._remaining_width = 70.0, 90.0
  renderer._init_state()
  feed(env, nav_message())
  renderer.render_card(CONTENT)
  assert drawn["rounded"][0].width == 8 + (70 + 12) + 6 + (90 + 12) + 8


def test_card_measures_with_the_font_it_draws_even_when_ui_language_needs_fallback(env, monkeypatch):
  # zh/ja/ko/th 界面：application 的 font_fallback 会把字体换成回退字体；卡片绕过它，测宽字体 = 绘制字体
  monkeypatch.setattr(application_module.multilang, "_language", "zh-CHS", raising=False)
  monkeypatch.setattr(application_module.gui_app, "fallback_font", lambda: "Noto-Fallback", raising=False)
  measured, drawn = [], capture_draws(monkeypatch)
  monkeypatch.setattr(module.rl, "measure_text_ex", lambda font, text, size, spacing: (measured.append(font), SimpleNamespace(x=10.0, y=size))[1])
  feed(env, nav_message(maneuver=26, exit_index=3))
  env[1].render_card(CONTENT)
  assert measured and set(measured) <= {"Inter-Medium", "Inter-Bold"}
  assert set(drawn["text_fonts"]) <= {"Inter-Medium", "Inter-Bold"}
  assert "Noto-Fallback" not in measured + drawn["text_fonts"]


def test_overflow_spellings_are_drawn_for_sentinel_values(env, monkeypatch):
  drawn = capture_draws(monkeypatch)
  feed(env, nav_message(distance=0xFFFFFFFE, remaining_m=0xFFFFFFFE, remaining_s=0xFFFFFFFE))
  env[1].render_card(CONTENT)
  assert drawn["text"] == ["999+ km", "99+ h", "999+ km"]


CHAR_WIDTHS = {" ": 3, ".": 3, "+": 8, "m": 13, "k": 9, "i": 4, "n": 10, "h": 9, "f": 6, "t": 5}


def uneven_text_width(font, text, size, spacing):
  # 数字宽度各不相同（像 Inter），最宽写法不是「看起来最长」的那条
  return SimpleNamespace(x=sum(CHAR_WIDTHS.get(c, 8 + (int(c) if c.isdigit() else 0)) for c in text) * size / 10, y=size)


def test_reserved_widths_cover_every_text_the_formatters_can_print(monkeypatch):
  monkeypatch.setattr(module.rl, "measure_text_ex", uneven_text_width)
  time_width, remaining_width = module.reserved_text_widths("Inter-Medium")
  size = module.ETA_FONT_SIZE * module.FONT_SCALE
  for seconds in range(0, 400_000, 13):
    assert uneven_text_width(None, format_minutes(seconds), size, 0).x <= time_width
  for is_metric, limit in ((True, 1_100_000), (False, 1_700_000)):
    for meters in range(0, limit, 7):
      assert uneven_text_width(None, format_distance(meters, is_metric), size, 0).x <= remaining_width


def test_card_text_lines_fit_inside_their_rows():
  # raylib 字号是行高：ETA 行要放进 26 高的块，距离行要在 ETA 块（y=50）之上
  assert module.ETA_TEXT_TOP + module.ETA_FONT_SIZE * module.FONT_SCALE <= module.BLOCK_HEIGHT
  assert module.DISTANCE_TEXT_TOP + module.DISTANCE_FONT_SIZE * module.FONT_SCALE <= module.BLOCK_TOP
  assert module.PHRASE_TEXT_TOP + module.PHRASE_FONT_SIZE * module.FONT_SCALE <= module.CARD_HEIGHT_SHORT


class RecordingRenderer:
  def __init__(self, name, calls, **extra):
    self._name, self._calls, self._extra = name, calls, extra

  def render(self, rect):
    self._calls.append(self._name)

  def render_nav_card(self, rect):
    self._calls.append("nav_card")

  def render_side_panel(self, rect):
    pass

  def set_can_draw_top_icons(self, value):
    pass

  def set_wheel_critical_icon(self, value):
    pass

  def will_render(self):
    return self._extra["alert"], True


def road_view_calls(monkeypatch, alert):
  calls = []
  view = road_view_module.AugmentedRoadView.__new__(road_view_module.AugmentedRoadView)
  view._rect = SCREEN
  view.texture_y = view.texture_uv = None   # CameraView.__del__ → close() 会读
  view.egl_images, view.egl_texture, view.shader, view.frame, view.available_streams = {}, None, None, None, []
  view._model_renderer = RecordingRenderer("model", calls)
  view._alert_renderer = RecordingRenderer("alert", calls, alert=alert)
  view._hud_renderer = RecordingRenderer("hud", calls)
  view._confidence_ball = RecordingRenderer("ball", calls)
  view._fade_texture = None
  monkeypatch.setattr(ui_state, "started", True, raising=False)
  monkeypatch.setattr(ui_state, "sm", {"selfdriveState": SimpleNamespace(experimentalMode=False), "carState": SimpleNamespace(vEgo=0.0)})
  monkeypatch.setattr(road_view_module.gui_app, "sunnypilot_ui", lambda: True)
  monkeypatch.setattr(road_view_module.CameraView, "_render", lambda self, rect: None)
  for name in ("_switch_stream_if_needed", "_update_calibration"):
    monkeypatch.setattr(road_view_module.AugmentedRoadView, name, lambda self, *a: None)
  for name in ("begin_scissor_mode", "end_scissor_mode", "draw_texture_ex", "draw_rectangle_rounded_lines_ex"):
    monkeypatch.setattr(road_view_module.rl, name, lambda *a: None)
  view._render(None)
  return calls


def test_nav_card_is_drawn_after_alert_and_hud_when_no_alert(monkeypatch):
  assert road_view_calls(monkeypatch, alert=None) == ["model", "alert", "hud", "nav_card", "ball"]


def test_nav_card_yields_to_any_alert_including_fade_out(monkeypatch):
  # will_render() 在警告淡出期仍返回上一条警告，所以「有返回值」即覆盖淡出期
  calls = road_view_calls(monkeypatch, alert=SimpleNamespace(visual_alert=0))
  assert calls == ["model", "alert", "hud", "ball"]
