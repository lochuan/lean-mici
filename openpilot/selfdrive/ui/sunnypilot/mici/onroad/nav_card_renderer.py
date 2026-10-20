"""
C4 导航：竖条顶部「导航」图标 + 右上 A1 卡片（宽度按最宽文字量出，当前 216；高 82 / 50）。判定与格式化见 sunnypilot/nav_card.py。
消息到来时（20 Hz）才重算内容与布局；每帧只做 fresh 判断和绘制，不读 Param（单位用 ui_state.is_metric 的缓存）。
"""
import math
import time

import pyray as rl

from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.sunnypilot.nav_arrow_shapes import arrow_shape
from openpilot.selfdrive.ui.sunnypilot.nav_card import (
  Arrow, CardContent, CardKind, NavIcon, arrived_visible, card_content, is_fresh, nav_icon,
  possible_remaining_texts, possible_time_texts,
)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FONT_SCALE, FontWeight, gui_app

ACCENT = rl.Color(155, 230, 185, 255)
MUTED = rl.Color(162, 175, 185, 255)
DIM = rl.Color(92, 104, 112, 255)
RED = rl.Color(210, 90, 90, 255)
GRAY = rl.Color(110, 118, 125, 255)
WHITE = rl.WHITE
CARD_BACKGROUND = rl.Color(7, 13, 18, 224)   # rgba(7,13,18,.88)
BLOCK_BACKGROUND = rl.Color(255, 255, 255, 23)   # rgba(255,255,255,.09)
PIN_FILL = rl.Color(7, 13, 18, 255)

CARD_RIGHT = 460.0   # 距内容区右缘 16
CARD_TOP = 16.0
CARD_RADIUS = 14.0
CARD_HEIGHT_FULL = 82.0
CARD_HEIGHT_SHORT = 50.0   # 重新规划 / 定位丢失 / 已到达：无 ETA 块
ARROW_SIZE = 42.0          # 62×60 视口按 42 高缩放
ARROW_LEFT = 8.0
ARROW_TOP = 4.0
TEXT_LEFT = 59.0
# raylib 的字号是行高（Inter 的 ascent−descent = 1.21 em），实际行高 = 字号 × FONT_SCALE；文字顶 = 行顶
DISTANCE_FONT_SIZE = 30    # 行高 34.8 px，Bold
ETA_FONT_SIZE = 21         # 行高 24.4 px，Medium
PHRASE_FONT_SIZE = 23      # 行高 26.7 px，Bold
DISTANCE_TEXT_TOP = 7.7    # 各行中线保持不变（距离 y+25.1、短语 y+24.6、ETA 块内居中）
PHRASE_TEXT_TOP = 11.3
ETA_TEXT_TOP = 0.8
BLOCK_TOP = 50.0
BLOCK_HEIGHT = 26.0
BLOCK_RADIUS = 8.0
BLOCK_PAD = 6.0
BLOCK_GAP = 6.0
EXIT_FONT_SIZE = 15
ICON_SIZE = 22.0
ICON_CENTER_Y = 26.0


def _measure(font: rl.Font, text: str, size: float) -> float:
  """文字宽度。直接用传入的字体，不经 font_fallback：绘制也用同一个 Inter（见 NavCardRenderer._text），
  所以任何界面语言（含 zh/ja/ko/th）下测宽字体 = 绘制字体。只在新消息 / 初始化时调用。"""
  return rl.measure_text_ex(font, text, size * FONT_SCALE, 0).x  # noqa: TID251


def reserved_text_widths(font: rl.Font) -> tuple[float, float]:
  """ETA 两块文字的预留宽度：从格式化函数枚举出所有可能的写法逐条测宽，取最宽（+1）。只在初始化时调用。"""
  time_width = max(_measure(font, text, ETA_FONT_SIZE) for text in possible_time_texts())
  remaining_width = max(_measure(font, text, ETA_FONT_SIZE) for text in possible_remaining_texts())
  return time_width + 1, remaining_width + 1


def _rounded(rect: rl.Rectangle, radius: float, color: rl.Color) -> None:
  rl.draw_rectangle_rounded(rect, 2 * radius / min(rect.width, rect.height), 8, color)


def _draw_polyline(points: list[rl.Vector2], thickness: float, color: rl.Color) -> None:
  for a, b in zip(points, points[1:], strict=False):
    rl.draw_line_ex(a, b, thickness, color)
  for p in points:
    rl.draw_circle_v(p, thickness / 2, color)


class NavCardRenderer:
  def __init__(self):
    self._bold = gui_app.font(FontWeight.BOLD)
    self._medium = gui_app.font(FontWeight.MEDIUM)
    self._time_width, self._remaining_width = reserved_text_widths(self._medium)
    self._init_state()

  def _init_state(self) -> None:
    self.card_width = math.ceil(ARROW_LEFT + (self._time_width + 12) + BLOCK_GAP + (self._remaining_width + 12) + ARROW_LEFT)
    self._icon = NavIcon.HIDDEN
    self._content: CardContent | None = None
    self._arrived_since_ns: int | None = None
    self._time_block_width = 0.0
    self._remaining_block_width = 0.0

  # ---- 状态 ----
  def update(self) -> None:
    sm = ui_state.sm
    if sm.updated['navStateSP']:
      self._on_message(sm['navStateSP'], sm.logMonoTime['navStateSP'])

  def _on_message(self, message, log_mono_ns: int) -> None:
    nav = message.navState
    status = str(nav.status)
    if status == "arrived":
      if self._arrived_since_ns is None:
        self._arrived_since_ns = log_mono_ns
    else:
      self._arrived_since_ns = None
    has_nav_state = message.hasNavState
    self._icon = nav_icon(has_nav_state, status)
    self._content = card_content(
      has_nav_state=has_nav_state, status=status, maneuver_type=nav.maneuverType,
      distance_to_maneuver_m=nav.distanceToManeuverM, route_remaining_m=nav.routeRemainingM,
      route_remaining_s=nav.routeRemainingSeconds, roundabout_exit_index=nav.roundaboutExitIndex,
      is_metric=ui_state.is_metric,
      arrived_card_visible=self._arrived_since_ns is not None and arrived_visible(self._arrived_since_ns, log_mono_ns))
    content = self._content
    self._time_block_width = _measure(self._medium, content.time_text, ETA_FONT_SIZE) + 12 if content and content.time_text else 0.0
    self._remaining_block_width = _measure(self._medium, content.remaining_text, ETA_FONT_SIZE) + 12 if content and content.remaining_text else 0.0

  def _fresh(self) -> bool:
    sm = ui_state.sm
    return is_fresh(time.monotonic(), sm.recv_time['navStateSP'], sm.seen['navStateSP'])

  def icon(self) -> NavIcon:
    return self._icon if self._fresh() else NavIcon.HIDDEN

  def card(self) -> CardContent | None:
    return self._content if self._fresh() else None

  # ---- 绘制 ----
  def render_icon(self, rect: rl.Rectangle) -> None:
    """画在右竖条（x 居中、cy=26，22×22），rect 为整屏。"""
    form = self.icon()
    if form == NavIcon.HIDDEN:
      return
    scale = ICON_SIZE / 24
    left = rect.x + rect.width - SIDE_PANEL_WIDTH / 2 - ICON_SIZE / 2
    top = rect.y + ICON_CENTER_Y - ICON_SIZE / 2

    def pt(x: float, y: float) -> rl.Vector2:
      return rl.Vector2(left + x * scale, top + y * scale)

    color = ACCENT if form == NavIcon.GREEN else GRAY
    outline = [pt(12, 2), pt(20, 21), pt(12, 16.5), pt(4, 21), pt(12, 2)]
    _draw_polyline(outline, 2 * scale, color)
    if form == NavIcon.GREEN:
      rl.draw_circle_v(pt(19, 5), 4.2 * scale, rl.BLACK)
      rl.draw_ring(pt(19, 5), (4.2 - 0.8) * scale, (4.2 + 0.8) * scale, 0.0, 360.0, 16, color)
      _draw_polyline([pt(17, 5), pt(18.5, 6.6), pt(21.1, 3.6)], 1.6 * scale, color)
    else:
      _draw_polyline([pt(3, 3), pt(21, 21)], 2 * scale, RED)

  def render_card(self, content_rect: rl.Rectangle) -> None:
    """画在内容区内（HUD 之上）；调用方在出警告条时不调用。"""
    content = self.card()
    if content is None:
      return
    x = content_rect.x + CARD_RIGHT - self.card_width
    y = content_rect.y + CARD_TOP
    short = content.kind != CardKind.NAVIGATING
    _rounded(rl.Rectangle(x, y, self.card_width, CARD_HEIGHT_SHORT if short else CARD_HEIGHT_FULL), CARD_RADIUS, CARD_BACKGROUND)

    if content.kind == CardKind.NAVIGATING:
      if content.arrow is not None:
        self._draw_arrow(content.arrow, x + ARROW_LEFT, y + ARROW_TOP, ACCENT, content.exit_index)
      if content.distance_text is not None:
        self._text(self._bold, content.distance_text, x + TEXT_LEFT, y + DISTANCE_TEXT_TOP, DISTANCE_FONT_SIZE, WHITE)
      self._draw_eta_blocks(content, x, y)
    elif content.kind == CardKind.ARRIVED:
      self._draw_arrow(Arrow.ARRIVE, x + ARROW_LEFT, y + ARROW_TOP, ACCENT, 0)
      self._text(self._bold, content.phrase, x + TEXT_LEFT, y + PHRASE_TEXT_TOP, PHRASE_FONT_SIZE, WHITE)
    else:
      lost = content.kind == CardKind.POSITION_LOST
      self._draw_arrow(Arrow.STRAIGHT, x + ARROW_LEFT, y + ARROW_TOP, DIM, 0)
      if lost:
        scale = ARROW_SIZE / 60
        _draw_polyline([rl.Vector2(x + ARROW_LEFT + 10 * scale, y + ARROW_TOP + 52 * scale),
                        rl.Vector2(x + ARROW_LEFT + 52 * scale, y + ARROW_TOP + 8 * scale)], 5 * scale, RED)
      self._text(self._bold, content.phrase, x + TEXT_LEFT, y + PHRASE_TEXT_TOP, PHRASE_FONT_SIZE, RED if lost else MUTED)

  def _draw_eta_blocks(self, content: CardContent, x: float, y: float) -> None:
    cursor = x + ARROW_LEFT
    top = y + BLOCK_TOP
    for text, width, color in ((content.time_text, self._time_block_width, ACCENT), (content.remaining_text, self._remaining_block_width, MUTED)):
      if text is None:
        continue
      _rounded(rl.Rectangle(cursor, top, width, BLOCK_HEIGHT), BLOCK_RADIUS, BLOCK_BACKGROUND)
      self._text(self._medium, text, cursor + BLOCK_PAD, top + ETA_TEXT_TOP, ETA_FONT_SIZE, color)
      cursor += width + BLOCK_GAP

  def _text(self, font: rl.Font, text: str, x: float, y: float, size: int, color: rl.Color) -> None:
    # 直接走未打补丁的 draw_text_ex：补丁在 ja/ko/th/zh 界面会把 Inter 换成回退字体，与 _measure 用的字体不一致
    draw = getattr(rl, "_orig_draw_text_ex", rl.draw_text_ex)
    draw(font, text, rl.Vector2(x, y), size * FONT_SCALE, 0, color)

  def _draw_arrow(self, arrow: Arrow, left: float, top: float, color: rl.Color, exit_index: int) -> None:
    scale = ARROW_SIZE / 60
    shape = arrow_shape(arrow)

    def pt(point: tuple[float, float]) -> rl.Vector2:
      return rl.Vector2(left + (31 + 1.4 * point[0]) * scale, top + (30 + 1.4 * point[1]) * scale)

    ghost_color = rl.Color(color.r, color.g, color.b, 97)
    for line in shape.ghost:
      _draw_polyline([pt(p) for p in line], 7 * scale, ghost_color)
    if shape.ring is not None:
      center = pt((shape.ring[0], shape.ring[1]))
      radius = shape.ring[2] * 1.4 * scale
      rl.draw_ring(center, radius - 2.5 * scale, radius + 2.5 * scale, 0.0, 360.0, 24, rl.Color(color.r, color.g, color.b, 128))
      if exit_index > 0:
        text = str(exit_index)
        half_width = _measure(self._bold, text, EXIT_FONT_SIZE) / 2
        self._text(self._bold, text, center.x - half_width, center.y - EXIT_FONT_SIZE * FONT_SCALE / 2, EXIT_FONT_SIZE, color)
    if shape.pin:
      center = pt((0, -4))
      _draw_polyline([pt((-6.5, 2)), pt((0, 13)), pt((6.5, 2))], 7 * scale, color)
      rl.draw_circle_v(center, 8 * 1.4 * scale + 3.5 * scale, color)
      rl.draw_circle_v(center, 8 * 1.4 * scale - 3.5 * scale, PIN_FILL)
      rl.draw_circle_v(center, 4 * scale, color)
    for line in shape.polylines:
      _draw_polyline([pt(p) for p in line], 7 * scale, color)
