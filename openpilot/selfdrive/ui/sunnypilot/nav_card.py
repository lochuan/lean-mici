"""C4 导航卡片 / 竖条「导航」图标的纯判定与格式化（无 UI 依赖，规则见 A1 票 §2）。

Valhalla 机动类型取 3.9.1 `proto/descriptors/directions.proto` 的 `DirectionsLeg.Maneuver.Type`（0–45，45 = kParkVehicle）。
"""
import math
from dataclasses import dataclass
from enum import Enum, auto

FRESH_SECONDS = 2.0
ARRIVED_VISIBLE_SECONDS = 5.0
INVALID_U32 = 0xFFFFFFFF
INVALID_MANEUVER = 255
METER_TO_FOOT = 3.28084
METERS_PER_MILE = 1609.344


class Arrow(Enum):
  STRAIGHT = auto()
  LEFT = auto()
  RIGHT = auto()
  SLIGHT_LEFT = auto()
  SLIGHT_RIGHT = auto()
  SHARP_LEFT = auto()
  SHARP_RIGHT = auto()
  UTURN_LEFT = auto()
  UTURN_RIGHT = auto()
  ROUNDABOUT = auto()
  FORK_LEFT = auto()
  FORK_RIGHT = auto()
  ARRIVE = auto()


class NavIcon(Enum):
  HIDDEN = auto()
  SLASHED = auto()   # 灰色红斜线
  GREEN = auto()


class CardKind(Enum):
  NAVIGATING = auto()
  REROUTING = auto()
  POSITION_LOST = auto()
  ARRIVED = auto()
  OFF_ROUTE = auto()   # status 2 但没有任何可画的内容：手机偏离锁存期间（>30 m 起，回到 ≤25 m 满 1 s 止，含判重算前与重算失败沿用旧路线）


_A = Arrow
# 下标 = Valhalla 机动类型；None = 不画箭头（0 kNone、28–36 渡轮/公交、39–45 电梯/台阶/楼宇/换层/停车）
_MANEUVER_ARROWS: tuple[Arrow | None, ...] = (
  None, _A.STRAIGHT, _A.STRAIGHT, _A.STRAIGHT, _A.ARRIVE, _A.ARRIVE, _A.ARRIVE, _A.STRAIGHT, _A.STRAIGHT,  # 0–8
  _A.SLIGHT_RIGHT, _A.RIGHT, _A.SHARP_RIGHT, _A.UTURN_RIGHT, _A.UTURN_LEFT, _A.SHARP_LEFT, _A.LEFT, _A.SLIGHT_LEFT,  # 9–16
  _A.STRAIGHT, _A.FORK_RIGHT, _A.FORK_LEFT, _A.FORK_RIGHT, _A.FORK_LEFT,  # 17–21 匝道直行/右/左、出口右/左
  _A.STRAIGHT, _A.FORK_RIGHT, _A.FORK_LEFT, _A.STRAIGHT, _A.ROUNDABOUT, _A.ROUNDABOUT,  # 22–27
  None, None, None, None, None, None, None, None, None,  # 28–36
  _A.SLIGHT_RIGHT, _A.SLIGHT_LEFT,  # 37 kMergeRight、38 kMergeLeft
  None, None, None, None, None, None, None,  # 39–45
)
ROUNDABOUT_ENTER = 26


def arrow_for_maneuver(maneuver_type: int) -> Arrow | None:
  return _MANEUVER_ARROWS[maneuver_type] if 0 <= maneuver_type < len(_MANEUVER_ARROWS) else None


def is_fresh(now: float, recv_time: float, ever_received: bool) -> bool:
  """最近一条 navStateSP 距今 ≤ 2 s（now 与 recv_time 同为 time.monotonic()，不按 UI 处理时刻）。"""
  return ever_received and now - recv_time <= FRESH_SECONDS


def nav_icon(has_nav_state: bool, status: str) -> NavIcon:
  """只表示导航本身的状态，与弯道减速 / navOk / SCC-map 无关；新鲜度由调用方（渲染器）另判。"""
  if not has_nav_state:
    return NavIcon.HIDDEN
  if status in ("positionLost", "mapMissing", "fault"):
    return NavIcon.SLASHED
  if status in ("navigating", "rerouting"):
    return NavIcon.GREEN
  return NavIcon.HIDDEN


def arrived_visible(first_arrived_log_mono_ns: int, log_mono_ns: int) -> bool:
  """到达卡片从第一次看到 arrived 的 logMonoTime 起显示 5 s。"""
  return log_mono_ns - first_arrived_log_mono_ns < int(ARRIVED_VISIBLE_SECONDS * 1e9)


MAX_DECIMAL_DISTANCE = 300.0   # km / mi：不到此值带一位小数（最宽 "299.9 km"），到此值起取整
MAX_DISTANCE = 1000            # km / mi：取整后到此值显示 "999+"
MAX_MINUTES_SHOWN = 200        # 不到此值写 "199 min"，到此值起改写小时
MAX_HOURS_SHOWN = 99.5         # 取整后到此值显示 "99+ h"
MAX_HOURS_SHOWN_MINUTES = int(MAX_HOURS_SHOWN * 60)


def _round_half_up(value: float) -> int:
  """普通四舍五入（345 m → 350 m），不用 Python round 的银行家舍入。"""
  return math.floor(value + 0.5)


def _big_distance(value: float, unit: str) -> str:
  tenths = _round_half_up(value * 10)
  if tenths < MAX_DECIMAL_DISTANCE * 10:
    return f"{tenths / 10:.1f} {unit}"
  whole = _round_half_up(value)
  return f"999+ {unit}" if whole >= MAX_DISTANCE else f"{whole} {unit}"


def format_distance(meters: int, is_metric: bool) -> str:
  """先四舍五入再选单位（995–999 m → "1.0 km"）；超上限写 "999+ km"，保证不超出卡片。"""
  if is_metric:
    small = _round_half_up(meters / 10) * 10
    return f"{small} m" if small < 1000 else _big_distance(meters / 1000, "km")
  feet = _round_half_up(meters * METER_TO_FOOT / 50) * 50
  return f"{feet} ft" if feet < 1000 else _big_distance(meters / METERS_PER_MILE, "mi")


def format_minutes(seconds: int) -> str:
  minutes = max(1, _round_half_up(seconds / 60))
  if minutes < MAX_MINUTES_SHOWN:
    return f"{minutes} min"
  hours = minutes / 60
  if hours >= MAX_HOURS_SHOWN:
    return "99+ h"
  tenths = _round_half_up(hours * 10)
  return f"{tenths / 10:.1f} h" if tenths < 100 else f"{_round_half_up(hours)} h"


def possible_time_texts() -> set[str]:
  """剩余时间块可能写出的全部文字（每分钟扫一遍，覆盖分钟、小时、"99+ h"），用来量最宽的一条。"""
  return {format_minutes(minutes * 60) for minutes in range(MAX_HOURS_SHOWN_MINUTES + 1)}


def possible_remaining_texts() -> set[str]:
  """剩余距离块可能写出的全部文字（公制 / 英制各扫一遍，覆盖米、英尺、一位小数、取整、"999+"）。"""
  texts = {format_distance(meters, True) for meters in range(0, 1000, 10)}
  texts |= {format_distance(tenth * 100, True) for tenth in range(10 * MAX_DISTANCE + 1)}
  texts |= {format_distance(round(feet / METER_TO_FOOT), False) for feet in range(0, 1000, 50)}
  texts |= {format_distance(round(tenth * METERS_PER_MILE / 10), False) for tenth in range(10 * MAX_DISTANCE + 1)}
  return texts


@dataclass(frozen=True)
class CardContent:
  kind: CardKind
  arrow: Arrow | None = None
  exit_index: int = 0
  distance_text: str | None = None
  time_text: str | None = None
  remaining_text: str | None = None
  phrase: str | None = None


def card_content(has_nav_state: bool, status: str, maneuver_type: int, distance_to_maneuver_m: int,
                 route_remaining_m: int, route_remaining_s: int, roundabout_exit_index: int, is_metric: bool,
                 arrived_card_visible: bool) -> CardContent | None:
  if not has_nav_state:
    return None
  if status == "navigating":
    content = CardContent(
      CardKind.NAVIGATING,
      arrow=arrow_for_maneuver(maneuver_type),
      exit_index=roundabout_exit_index if maneuver_type == ROUNDABOUT_ENTER else 0,
      distance_text=None if distance_to_maneuver_m == INVALID_U32 else format_distance(distance_to_maneuver_m, is_metric),
      time_text=None if route_remaining_s == INVALID_U32 else format_minutes(route_remaining_s),
      remaining_text=None if route_remaining_m == INVALID_U32 else format_distance(route_remaining_m, is_metric))
    # 手机偏离路线时机动 255、距离与剩余值全是无效值（协议无区分「刚偏离」与「重算失败」的字段）：
    # 全高卡片里什么都没有，改写短卡片提示。在路线上时剩余距离 / 时间恒有效，所以不会误触发
    if content.arrow is None and content.distance_text is None and content.time_text is None and content.remaining_text is None:
      return CardContent(CardKind.OFF_ROUTE, phrase="Off route")
    return content
  if status == "rerouting":
    return CardContent(CardKind.REROUTING, phrase="Rerouting")
  if status == "positionLost":
    return CardContent(CardKind.POSITION_LOST, phrase="GPS lost")
  if status == "arrived" and arrived_card_visible:
    return CardContent(CardKind.ARRIVED, arrow=Arrow.ARRIVE, phrase="Arrived")
  return None
