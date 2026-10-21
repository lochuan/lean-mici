"""导航卡片 / 竖条导航图标的纯判定与格式化（无 UI 依赖）。
  pytest openpilot/selfdrive/ui/tests/test_nav_card.py
"""
import pytest

from openpilot.selfdrive.ui.sunnypilot.nav_card import (
  Arrow, CardKind, NavIcon, ARRIVED_VISIBLE_SECONDS, FRESH_SECONDS, INVALID_U32, INVALID_MANEUVER,
  arrow_for_maneuver, card_content, format_distance, format_minutes, is_fresh, nav_icon, arrived_visible,
  possible_remaining_texts, possible_time_texts,
)

EXPECTED_ARROWS = {
  **dict.fromkeys([1, 2, 3, 7, 8, 17, 22, 25], Arrow.STRAIGHT),
  **dict.fromkeys([4, 5, 6], Arrow.ARRIVE),
  9: Arrow.SLIGHT_RIGHT, 10: Arrow.RIGHT, 11: Arrow.SHARP_RIGHT, 12: Arrow.UTURN_RIGHT, 13: Arrow.UTURN_LEFT,
  14: Arrow.SHARP_LEFT, 15: Arrow.LEFT, 16: Arrow.SLIGHT_LEFT,
  **dict.fromkeys([18, 20, 23], Arrow.FORK_RIGHT), **dict.fromkeys([19, 21, 24], Arrow.FORK_LEFT),
  **dict.fromkeys([26, 27], Arrow.ROUNDABOUT), 37: Arrow.SLIGHT_RIGHT, 38: Arrow.SLIGHT_LEFT,
}


@pytest.mark.parametrize("maneuver", range(46))
def test_every_valhalla_maneuver_type_maps(maneuver):
  assert arrow_for_maneuver(maneuver) == EXPECTED_ARROWS.get(maneuver)  # 缺省 = 不画箭头


@pytest.mark.parametrize("maneuver", [46, 254, INVALID_MANEUVER])
def test_invalid_or_unknown_maneuver_draws_no_arrow(maneuver):
  assert arrow_for_maneuver(maneuver) is None


@pytest.mark.parametrize("age, fresh", [(0.0, True), (FRESH_SECONDS, True), (FRESH_SECONDS + 0.01, False)])
def test_fresh_boundary(age, fresh):
  assert is_fresh(now=100.0 + age, recv_time=100.0, ever_received=True) is fresh


def test_never_received_is_not_fresh():
  assert not is_fresh(now=1.0, recv_time=0.0, ever_received=False)


@pytest.mark.parametrize("status, expected", [
  ("notRunning", NavIcon.HIDDEN), ("idle", NavIcon.HIDDEN), ("arrived", NavIcon.HIDDEN),
  ("positionLost", NavIcon.SLASHED), ("mapMissing", NavIcon.SLASHED), ("fault", NavIcon.SLASHED),
  ("navigating", NavIcon.GREEN), ("rerouting", NavIcon.GREEN),
])
def test_icon_by_status(status, expected):
  assert nav_icon(has_nav_state=True, status=status) == expected


@pytest.mark.parametrize("status", ["navigating", "positionLost", "fault"])
def test_icon_hidden_without_nav_state(status):
  assert nav_icon(has_nav_state=False, status=status) == NavIcon.HIDDEN


def card(status="navigating", maneuver=15, distance=350, remaining_m=8400, remaining_s=720, **kw):
  args = {"has_nav_state": True, "status": status, "maneuver_type": maneuver, "distance_to_maneuver_m": distance,
          "route_remaining_m": remaining_m, "route_remaining_s": remaining_s, "roundabout_exit_index": 0, "is_metric": True,
          "arrived_card_visible": True}
  args.update(kw)
  return card_content(**args)


def test_navigating_card_has_arrow_distance_and_two_eta_blocks():
  c = card()
  assert (c.kind, c.arrow, c.distance_text, c.time_text, c.remaining_text, c.phrase) == (
    CardKind.NAVIGATING, Arrow.LEFT, "350 m", "12 min", "8.4 km", None)


def test_navigating_roundabout_carries_exit_index_only_for_enter():
  assert card(maneuver=26, roundabout_exit_index=3).exit_index == 3
  assert card(maneuver=27, roundabout_exit_index=3).exit_index == 0   # kRoundaboutExit 不写出口号
  assert card(maneuver=26, roundabout_exit_index=0).exit_index == 0


def test_sentinels_hide_only_their_item():
  assert card(distance=INVALID_U32).distance_text is None
  assert card(distance=INVALID_U32).time_text == "12 min"
  assert card(remaining_s=INVALID_U32).time_text is None
  assert card(remaining_s=INVALID_U32).remaining_text == "8.4 km"
  assert card(remaining_m=INVALID_U32).remaining_text is None
  assert card(maneuver=INVALID_MANEUVER).arrow is None
  assert card(maneuver=INVALID_MANEUVER).distance_text == "350 m"


def test_no_arrow_types_keep_distance_and_eta():
  c = card(maneuver=0)
  assert (c.arrow, c.distance_text, c.time_text, c.remaining_text) == (None, "350 m", "12 min", "8.4 km")


def off_route_card(**kw):
  """手机偏离路线 / 重算失败沿用旧路线：status 2，机动 255，距离与剩余值全是无效值（E3-3-21）。"""
  args = {"maneuver": INVALID_MANEUVER, "distance": INVALID_U32, "remaining_m": INVALID_U32, "remaining_s": INVALID_U32}
  args.update(kw)
  return card(**args)


def test_navigating_with_nothing_to_show_says_off_route_instead_of_blank_card():
  c = off_route_card()
  assert c is not None and c.phrase == "Off route"
  assert (c.kind, c.arrow, c.exit_index, c.distance_text, c.time_text, c.remaining_text) == (CardKind.OFF_ROUTE, None, 0, None, None, None)


@pytest.mark.parametrize("maneuver", [0, 28, 39, 45, 46, 254, INVALID_MANEUVER])
def test_every_no_arrow_maneuver_with_nothing_else_to_show_says_off_route(maneuver):
  assert off_route_card(maneuver=maneuver).kind == CardKind.OFF_ROUTE


def test_maneuver_invalid_but_distance_valid_keeps_navigating_card_without_arrow():
  c = off_route_card(distance=350)
  assert (c.kind, c.arrow, c.distance_text, c.phrase) == (CardKind.NAVIGATING, None, "350 m", None)


def test_maneuver_valid_but_distance_invalid_keeps_arrow_card():
  c = off_route_card(maneuver=15)
  assert (c.kind, c.arrow, c.distance_text, c.phrase) == (CardKind.NAVIGATING, Arrow.LEFT, None, None)


@pytest.mark.parametrize("kw, time_text, remaining_text", [
  ({"remaining_s": 720}, "12 min", None),
  ({"remaining_m": 8400}, None, "8.4 km"),
  ({"remaining_s": 720, "remaining_m": 8400}, "12 min", "8.4 km"),
  ({"remaining_s": 0, "remaining_m": 0}, "1 min", "0 m"),
])
def test_maneuver_and_distance_both_invalid_but_eta_valid_still_shows_eta_not_the_hint(kw, time_text, remaining_text):
  c = off_route_card(**kw)
  assert (c.kind, c.arrow, c.distance_text, c.time_text, c.remaining_text, c.phrase) == (CardKind.NAVIGATING, None, None, time_text, remaining_text, None)


@pytest.mark.parametrize("status", ["notRunning", "idle", "mapMissing", "fault"])
def test_off_route_hint_only_for_navigating_status(status):
  assert off_route_card(status=status) is None


def test_rerouting_and_position_lost_phrases_win_over_off_route_sentinels():
  assert off_route_card(status="rerouting").phrase == "Rerouting"
  assert off_route_card(status="positionLost").phrase == "GPS lost"


def test_rerouting_and_position_lost_cards_have_only_phrase():
  r, lost = card(status="rerouting"), card(status="positionLost")
  assert (r.kind, r.phrase, r.distance_text, r.time_text, r.remaining_text) == (CardKind.REROUTING, "Rerouting", None, None, None)
  assert (lost.kind, lost.phrase) == (CardKind.POSITION_LOST, "GPS lost")


def test_arrived_card_only_while_visible():
  assert card(status="arrived").kind == CardKind.ARRIVED
  assert card(status="arrived").phrase == "Arrived"
  assert card(status="arrived", arrived_card_visible=False) is None


@pytest.mark.parametrize("status", ["notRunning", "idle", "mapMissing", "fault"])
def test_other_statuses_draw_no_card(status):
  assert card(status=status) is None


def test_missing_nav_state_draws_no_card():
  assert card(has_nav_state=False) is None


def test_arrived_visible_window():
  start = 10_000_000_000
  assert arrived_visible(start, start)
  assert arrived_visible(start, start + int(ARRIVED_VISIBLE_SECONDS * 1e9) - 1)
  assert not arrived_visible(start, start + int(ARRIVED_VISIBLE_SECONDS * 1e9))


@pytest.mark.parametrize("meters, metric, text", [
  (0, True, "0 m"), (10, True, "10 m"), (50, True, "50 m"), (350, True, "350 m"), (354, True, "350 m"), (994, True, "990 m"),
  (995, True, "1.0 km"), (999, True, "1.0 km"), (1000, True, "1.0 km"), (8400, True, "8.4 km"), (99900, True, "99.9 km"),
  (288800, True, "288.8 km"), (299949, True, "299.9 km"), (299960, True, "300 km"), (412000, True, "412 km"),
  (999400, True, "999 km"), (999500, True, "999+ km"), (1_000_000, True, "999+ km"), (0xFFFFFFFE, True, "999+ km"),
  (60, False, "200 ft"), (91, False, "300 ft"), (297, False, "950 ft"), (298, False, "0.2 mi"), (299, False, "0.2 mi"),
  (500, False, "0.3 mi"), (303834, False, "188.8 mi"), (482800, False, "300 mi"), (1_609_344, False, "999+ mi"),
])
def test_format_distance(meters, metric, text):
  assert format_distance(meters, metric) == text


@pytest.mark.parametrize("seconds, text", [
  (0, "1 min"), (30, "1 min"), (60, "1 min"), (720, "12 min"), (11280, "188 min"), (11940, "199 min"),
  (12000, "3.3 h"), (36000, "10 h"), (360000, "99+ h"), (0xFFFFFFFE, "99+ h"),
])
def test_format_minutes(seconds, text):
  assert format_minutes(seconds) == text


@pytest.mark.parametrize("meters, text", [(5, "10 m"), (14, "10 m"), (15, "20 m"), (345, "350 m"), (355, "360 m"), (994, "990 m"), (300500, "301 km")])
def test_distance_rounds_half_up_not_to_even(meters, text):
  assert format_distance(meters, True) == text


@pytest.mark.parametrize("seconds, text", [(29, "1 min"), (30, "1 min"), (89, "1 min"), (90, "2 min"), (150, "3 min"), (210, "4 min")])
def test_minutes_round_half_up_not_to_even(seconds, text):
  assert format_minutes(seconds) == text


@pytest.mark.parametrize("seconds, text", [
  (35400, "9.8 h"), (35640, "9.9 h"), (35880, "10 h"), (35940, "10 h"), (36000, "10 h"), (358140, "99 h"), (358200, "99+ h"),
])
def test_hours_never_print_ten_point_zero(seconds, text):
  assert format_minutes(seconds) == text


def test_possible_texts_cover_every_formatter_output():
  # 步长与枚举互质的扫描：枚举漏掉任何一种写法（含超限写法）都会红
  times = possible_time_texts()
  assert {format_minutes(s) for s in range(0, 400_000, 13)} <= times
  remaining = possible_remaining_texts()
  assert {format_distance(m, True) for m in range(0, 1_100_000, 7)} <= remaining
  assert {format_distance(m, False) for m in range(0, 1_700_000, 7)} <= remaining


def test_possible_texts_include_the_overflow_spellings():
  assert {"1 min", "199 min", "3.3 h", "10 h", "99+ h"} <= possible_time_texts()
  assert {"0 m", "990 m", "1.0 km", "299.9 km", "300 km", "999 km", "999+ km", "950 ft", "0.2 mi", "299.9 mi", "999+ mi"} <= possible_remaining_texts()


@pytest.mark.parametrize("meters, text", [(50292, "31.3 mi"), (150876, "93.8 mi"), (704088, "438 mi")])
def test_imperial_exact_half_ties_round_up(meters, text):
  assert format_distance(meters, False) == text   # 31.25 mi 的整数倍，倒数相乘会少进一位


def test_exact_ties_at_the_300_and_ten_hour_boundaries():
  assert format_distance(299950, True) == "300 km"
  assert format_minutes(35820) == "10 h"   # 597 min = 9.95 h
