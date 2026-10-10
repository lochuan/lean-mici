"""诊断页的数据：本次行驶统计（跑偏 / 画龙 / 转向）+ 学习参数（实时优先，停车读缓存）。

对外只有 Diagnostics.update(sm, now) 与 report(params, torque_learned, now)。
约定：模型系 y 向右为正（ldw.py：左线 y[0] < 0），所有偏移 >0 表示偏右；
"车"是模型基准点（CameraOffset 正确时即车身中线），所以相机横向装偏在这里看不出来，
只能靠前端的"量一量"。判定阈值在前端 lib/diagnostics.ts，这里只出统计量。
"""
import math
from collections import deque

import numpy as np

from openpilot.cereal import log, messaging
from opendbc.car.structs import car
from openpilot.common.realtime import DT_CTRL, DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.locationd.calibrationd import PITCH_LIMITS, YAW_LIMITS

# report() 读到的全部 param：调用方可在锁外先读好，传 {key: value} 进来
PARAM_KEYS = ("CalibrationParams", "LiveParametersV2", "LiveDelay", "CarParamsPersistent", "CameraOffset")
LIVE_TIMEOUT = 5.0  # s；超过即视为该进程没在跑（停车），改读落盘缓存

# 直道采样条件（跑偏、画龙共用）
MIN_SPEED = 50 / 3.6
MAX_STRAIGHT_CURVATURE = 0.002
MIN_LANE_PROB = 0.7
LANE_WIDTH_RANGE = (2.5, 4.5)
PLAN_T = 1.5  # s；规划偏移取这一时刻的位置

# 画龙：10 s 不重叠窗口；去趋势后峰峰值 + 过零周期
WEAVE_WINDOW_S = 10.0
WEAVE_P2P_M = 0.30
WEAVE_PERIOD_RANGE = (1.5, 8.0)
WEAVE_HYSTERESIS_M = 0.03
TRACE_DT = 0.25
TRACE_LEN = 120  # 30 s

# 转向
MIN_STEER_SPEED = 5.0
CURVE_LAT_ACCEL = 1.0
USAGE_BINS = 5


def _parse_param_memoized(params, key: str, parse, memo: dict):
  """字节型 param -> parse(raw)；按字节记忆，避免每次请求重复解析。缺失或解析失败返回 None。"""
  raw = params.get(key)
  if not raw:
    return None
  raw = bytes(raw)
  if memo.get(key, (None,))[0] != raw:
    try:
      parsed = parse(raw)
    except Exception:
      cloudlog.exception(f"lanlink diagnostics: {key} 解析失败")
      parsed = None
    memo[key] = (raw, parsed)
  return memo[key][1]


def _cached_service_msg(params, key: str, service: str, memo: dict):
  """缓存 param（整条 Event 字节）-> 对应服务的消息。"""
  return _parse_param_memoized(params, key, lambda raw: getattr(messaging.log_from_bytes(raw, log.Event), service), memo)


def _car_params(params, memo: dict):
  return _parse_param_memoized(params, "CarParamsPersistent", lambda raw: messaging.log_from_bytes(raw, car.CarParams), memo)


def _weave_window(ts: list[float], ys: list[float]) -> tuple[float, float | None]:
  """一个窗口 -> (去线性趋势后的峰峰值 p95-p5, 周期 s 或 None)。周期由带滞回的过零间隔估计。"""
  t = np.asarray(ts)
  y = np.asarray(ys)
  y = y - np.polyval(np.polyfit(t - t[0], y, 1), t - t[0])
  p2p = float(np.percentile(y, 95) - np.percentile(y, 5))
  side, crossings = 0, []
  for ti, yi in zip(t, y, strict=True):
    s = 1 if yi > WEAVE_HYSTERESIS_M else -1 if yi < -WEAVE_HYSTERESIS_M else 0
    if s and s != side:
      if side:
        crossings.append(ti)
      side = s
  period = 2 * (crossings[-1] - crossings[0]) / (len(crossings) - 1) if len(crossings) >= 2 else None
  return p2p, period


class _DriveStats:
  """本次行驶的累计量；新行驶开始时整个替换。

  ponytail: 只在内存、仅本次行驶，设备重启即丢；需要跨行驶对比时，再把汇总落盘到新的 param。"""

  def __init__(self):
    self.drift_s = 0.0
    self.car_offset_sum = 0.0
    self.plan_offset_sum = 0.0
    self.drift_samples = 0
    self.segment_t: list[float] = []
    self.segment_y: list[float] = []
    self.windows: list[tuple[float, float | None]] = []
    self.trace: deque[float | None] = deque([None] * TRACE_LEN, maxlen=TRACE_LEN)
    self.trace_bucket: int | None = None
    self.last_model_t: float | None = None
    self.last_controls_t: float | None = None
    self.lateral_control = ""
    self.active_s = 0.0
    self.curve_s = 0.0
    self.saturated_curve_s = 0.0
    self.usage = [0.0] * USAGE_BINS
    self.max_lat_accel: float | None = None
    self.eps_temp_faults = 0
    self.eps_permanent = False
    self.prev_temp_fault = False
    self.car_state_seen = False
    self.accurate_angle = False


class Diagnostics:
  def __init__(self):
    self._started = False
    self._started_at: float | None = None
    self._ended_at: float | None = None
    self._stats = _DriveStats()
    self._live: dict[str, tuple[float, object]] = {}
    self._parsed_params: dict = {}

  # ---- 输入 ----

  def update(self, sm, now: float) -> None:
    if sm.updated["deviceState"]:
      started = bool(sm["deviceState"].started)
      if started and not self._started:
        self._stats = _DriveStats()
        self._started_at, self._ended_at = now, None
      elif self._started and not started:
        self._ended_at = now
      self._started = started
    for service in ("extrinsicsCalibration", "vehicleParameters", "lateralDelay"):
      if sm.updated[service]:
        self._live[service] = (now, sm[service])
    if sm.updated["carState"]:
      self._on_car_state(sm["carState"], sm["carControl"])
    if sm.updated["controlsState"]:
      self._on_controls(sm["controlsState"], sm["carState"], sm["carControl"], now)
    if sm.updated["modelV2"]:
      self._on_model(sm["modelV2"], sm["carState"], sm["carControl"], sm["controlsState"], now)

  def _on_car_state(self, cs, cc) -> None:
    stats = self._stats
    stats.car_state_seen = True
    if abs(cs.steeringAngleOffsetDeg) > 1e-3:
      stats.accurate_angle = True
    if cs.steerFaultTemporary and not stats.prev_temp_fault and cc.enabled:
      stats.eps_temp_faults += 1
    stats.prev_temp_fault = bool(cs.steerFaultTemporary)
    if cs.steerFaultPermanent:
      stats.eps_permanent = True

  def _on_controls(self, ctl, cs, cc, now: float) -> None:
    stats = self._stats
    dt = DT_CTRL if stats.last_controls_t is None else min(max(now - stats.last_controls_t, 0.0), 5 * DT_CTRL)
    stats.last_controls_t = now
    if not cc.latActive or cs.vEgo < MIN_STEER_SPEED:
      return
    kind = ctl.lateralControlState.which()
    state = getattr(ctl.lateralControlState, kind)
    stats.lateral_control = {"torqueState": "torque", "angleState": "angle"}.get(kind, "")
    v_ego_sq = cs.vEgo ** 2
    stats.active_s += dt
    lat_accel = abs(ctl.curvature) * v_ego_sq
    stats.max_lat_accel = lat_accel if stats.max_lat_accel is None else max(stats.max_lat_accel, lat_accel)
    if abs(ctl.desiredCurvature) * v_ego_sq > CURVE_LAT_ACCEL:
      stats.curve_s += dt
      if state.saturated:
        stats.saturated_curve_s += dt
    if kind == "torqueState":
      stats.usage[min(int(abs(state.output) * USAGE_BINS), USAGE_BINS - 1)] += dt

  def _on_model(self, model, cs, cc, ctl, now: float) -> None:
    stats = self._stats
    dt = DT_MDL if stats.last_model_t is None else min(max(now - stats.last_model_t, 0.0), 2 * DT_MDL)
    gap = stats.last_model_t is not None and now - stats.last_model_t > 4 * DT_MDL
    stats.last_model_t = now
    offsets = self._straight_offsets(model, cs, cc, ctl)

    bucket = math.floor(now / TRACE_DT)
    if stats.trace_bucket is None or bucket > stats.trace_bucket:
      missing = 0 if stats.trace_bucket is None else min(bucket - stats.trace_bucket - 1, TRACE_LEN)
      stats.trace.extend([None] * missing)
      stats.trace.append(None if offsets is None else round(offsets[0], 3))
      stats.trace_bucket = bucket

    if offsets is None or gap:
      stats.segment_t.clear()
      stats.segment_y.clear()
      if offsets is None:
        return
    car_offset, plan_offset = offsets
    stats.drift_s += dt
    stats.drift_samples += 1
    stats.car_offset_sum += car_offset
    stats.plan_offset_sum += plan_offset
    stats.segment_t.append(now)
    stats.segment_y.append(car_offset)
    if stats.segment_t[-1] - stats.segment_t[0] >= WEAVE_WINDOW_S - 1e-6:
      stats.windows.append(_weave_window(stats.segment_t, stats.segment_y))
      stats.segment_t.clear()
      stats.segment_y.clear()

  @staticmethod
  def _straight_offsets(model, cs, cc, ctl) -> tuple[float, float] | None:
    """满足直道采样条件时返回 (车相对车道中心, 规划相对车道中心)，米，>0 偏右；否则 None。"""
    if not cc.latActive or cs.vEgo < MIN_SPEED or cs.leftBlinker or cs.rightBlinker or cs.steeringPressed:
      return None
    if abs(ctl.desiredCurvature) >= MAX_STRAIGHT_CURVATURE:
      return None
    probs = list(model.laneLineProbs)
    lines = list(model.laneLines)
    if len(probs) < 3 or len(lines) < 3 or min(probs[1], probs[2]) < MIN_LANE_PROB:
      return None
    left, right = lines[1], lines[2]
    if not left.y or not right.y:
      return None
    width = right.y[0] - left.y[0]
    if not LANE_WIDTH_RANGE[0] <= width <= LANE_WIDTH_RANGE[1]:
      return None
    pos = model.position
    if not pos.t:
      return None
    plan_x = float(np.interp(PLAN_T, pos.t, pos.x))
    plan_y = float(np.interp(PLAN_T, pos.t, pos.y))
    center_at_plan = (np.interp(plan_x, left.x, left.y) + np.interp(plan_x, right.x, right.y)) / 2
    return -(left.y[0] + right.y[0]) / 2, plan_y - float(center_at_plan)

  # ---- 输出 ----

  def _fresh_or_cached(self, service: str, key: str, params, now: float):
    live = self._live.get(service)
    if live is not None and now - live[0] < LIVE_TIMEOUT:
      return "live", live[1]
    msg = _cached_service_msg(params, key, service, self._parsed_params)
    return ("cache", msg) if msg is not None else (None, None)

  def report(self, params, torque_learned: dict | None, now: float) -> dict:
    """params：任何带 .get(key) 的对象（Params 或 PARAM_KEYS 的 dict 快照）。"""
    stats = self._stats
    car_params = _car_params(params, self._parsed_params)
    if self._started_at is None:
      seconds = 0.0
    else:
      seconds = (self._ended_at if self._ended_at is not None else now) - self._started_at

    source, calib = self._fresh_or_cached("extrinsicsCalibration", "CalibrationParams", params, now)
    camera = None
    if calib is not None:
      rpy = list(calib.rpyCalib) or [0.0, 0.0, 0.0]
      camera = {
        "source": source,
        "calStatus": str(calib.calStatus),
        "calPerc": int(calib.calPerc),
        "pitchDeg": round(math.degrees(rpy[1]), 3),
        "yawDeg": round(math.degrees(rpy[2]), 3),
        "limits": {"pitchUpDeg": round(-math.degrees(PITCH_LIMITS[0]), 2),
                   "pitchDownDeg": round(math.degrees(PITCH_LIMITS[1]), 2),
                   "yawDeg": round(math.degrees(YAW_LIMITS[1]), 2)},
        "cameraOffsetM": float(params.get("CameraOffset") or 0.0),
      }

    _, vehicle_params = self._fresh_or_cached("vehicleParameters", "LiveParametersV2", params, now)
    _, lateral_delay = self._fresh_or_cached("lateralDelay", "LiveDelay", params, now)
    brand = str(car_params.brand) if car_params is not None else ""
    if brand != "toyota":
      accurate_angle_state = "n/a"
    elif not stats.car_state_seen:
      accurate_angle_state = "unknown"
    else:
      accurate_angle_state = "ready" if stats.accurate_angle else "pending"

    windows = stats.windows
    p2ps = [w[0] for w in windows]
    periods = [w[1] for w in windows if w[1] is not None]
    weave_windows = sum(1 for p2p, period in windows if p2p >= WEAVE_P2P_M and period is not None
                        and WEAVE_PERIOD_RANGE[0] <= period <= WEAVE_PERIOD_RANGE[1])

    def mean_drift_sample(total: float) -> float | None:
      return round(total / stats.drift_samples, 4) if stats.drift_samples else None

    def rounded_or_none(v, digits=3):
      return None if v is None else round(float(v), digits)

    return {
      "drive": {"started": self._started, "seconds": round(seconds, 1)},
      "camera": camera,
      "drift": {
        "seconds": round(stats.drift_s, 2),
        "carOffsetM": mean_drift_sample(stats.car_offset_sum),
        "planOffsetM": mean_drift_sample(stats.plan_offset_sum),
        "angleOffsetDeg": rounded_or_none(vehicle_params.angleOffsetAverageDeg) if vehicle_params is not None else None,
        "angleOffsetValid": bool(vehicle_params.angleOffsetAverageValid) if vehicle_params is not None else None,
        "accurateAngle": accurate_angle_state,
        "latAccelOffset": rounded_or_none(torque_learned.get("latAccelOffset"), 4) if torque_learned else None,
      },
      "weave": {
        "seconds": round(stats.drift_s, 2),
        "windows": len(windows),
        "weaveWindows": weave_windows,
        "medianP2pM": rounded_or_none(np.median(p2ps)) if p2ps else None,
        "medianPeriodS": rounded_or_none(np.median(periods), 2) if periods else None,
        "trace": list(stats.trace),
        "lateralDelayS": rounded_or_none(lateral_delay.lateralDelay) if lateral_delay is not None else None,
        "factoryDelayS": rounded_or_none(car_params.steerActuatorDelay) if car_params is not None else None,
      },
      "steering": {
        "control": stats.lateral_control,
        "activeSeconds": round(stats.active_s, 2),
        "curveSeconds": round(stats.curve_s, 2),
        "saturatedCurveSeconds": round(stats.saturated_curve_s, 2),
        "usage": [round(u, 2) for u in stats.usage] if stats.lateral_control == "torque" else None,
        "epsTempFaults": stats.eps_temp_faults,
        "epsPermanent": stats.eps_permanent,
        "maxLatAccel": rounded_or_none(stats.max_lat_accel),
      },
    }
