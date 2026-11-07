import json
import math
import platform
import time

from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import MIN_V

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState

ACTIVE_STATES = (MapState.turning, )
ENABLED_STATES = (MapState.enabled, MapState.overriding, *ACTIVE_STATES)

TARGET_JERK = -0.6  # m/s^3 There's some jounce limits that are not consistent so we're fudging this some
TARGET_ACCEL = -1.2  # m/s^2 should match up with the long planner limit
TARGET_OFFSET = 1.0  # seconds - This controls how soon before the curve you reach the target velocity. It also helps
                     # reach the target velocity when inaccuracies in the distance modeling logic would cause overshoot.
                     # The value is multiplied against the target velocity to determine the additional distance. This is
                     # done to keep the distance calculations consistent but results in the offset actually being less
                     # time than specified depending on how much of a speed differential there is between v_ego and the
                     # target velocity.


def calculate_accel(t, target_jerk, a_ego):
  return a_ego + target_jerk * t


def calculate_velocity(t, target_jerk, a_ego, v_ego):
  return v_ego + a_ego * t + target_jerk/2 * (t ** 2)


def calculate_distance(t, target_jerk, a_ego, v_ego):
  return t * v_ego + a_ego/2 * (t ** 2) + target_jerk/6 * (t ** 3)


class SmartCruiseControlMap:
  v_target: float = 0
  a_target: float = 0.
  v_ego: float = 0.
  a_ego: float = 0.
  output_v_target: float = V_CRUISE_UNSET
  output_a_target: float = 0.

  def __init__(self, *, clock=None):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.enabled = self.params.get_bool("SmartCruiseControlMap")
    self.long_enabled = False
    self.long_override = False
    self.is_enabled = False
    self.is_active = False
    self.state = MapState.disabled
    self.v_cruise = 0
    self.target_lat = 0.0
    self.target_lon = 0.0
    self.frame = -1
    self.clock = clock or (time.monotonic_ns if platform.system() == "Darwin" else
                           lambda: time.clock_gettime_ns(time.CLOCK_BOOTTIME))
    self.last_bad_log_ns = 0
    self.target_velocities = []
    self.snapshot = None
    self.sample_time_ns = 0
    self.travelled_m = 0.0
    self.last_update_ns = None
    self.motion_valid = True

  def get_v_target_from_control(self) -> float:
    if self.is_active:
      return max(self.v_target, MIN_V)

    return V_CRUISE_UNSET

  def get_a_target_from_control(self) -> float:
    return self.a_ego

  def update_params(self):
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("SmartCruiseControlMap")

  def clear_targets(self):
    self.snapshot = None
    self.target_velocities = []
    self.v_target = 0.0
    self.target_lat = self.target_lon = 0.0

  def read_targets(self, now_ns):
    raw = self.mem_params.get("MapTargetVelocities")
    if raw is None:
      return None
    try:
      snapshot = json.loads(raw)
      if snapshot == []:
        return None
      if not isinstance(snapshot, dict):
        raise ValueError("expected distance envelope")
      sample_ns, valid_until_ns = snapshot["sample_time_ns"], snapshot["valid_until_ns"]
      if (type(sample_ns) is not int or type(valid_until_ns) is not int or
          not 0 < sample_ns <= now_ns <= valid_until_ns <= sample_ns + 2_000_000_000):
        raise ValueError("invalid or expired sample times")
      points = snapshot["points"]
      if not isinstance(points, list) or not 1 <= len(points) <= 8:
        raise ValueError("invalid point count")
      previous_distance = 0
      for point in points:
        if not isinstance(point, dict):
          raise ValueError("invalid point")
        lat, lon, distance, velocity = (point[key] for key in ("latitude", "longitude", "distance_m", "velocity"))
        if not all(type(value) in (int, float) and math.isfinite(value) for value in (lat, lon, distance, velocity)):
          raise ValueError("non-finite point")
        if (not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0) or
            not previous_distance <= distance <= 600 or velocity <= 0):
          raise ValueError("invalid coordinates, distance or velocity")
        previous_distance = distance
      return snapshot
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
      if now_ns - self.last_bad_log_ns >= 5_000_000_000 or self.last_bad_log_ns == 0:
        cloudlog.warning("SCC-map rejected targets: %s", error)
        self.last_bad_log_ns = now_ns
      return None

  def update_calculations(self) -> None:
    now_ns = self.clock()
    previous_ns = self.last_update_ns
    self.last_update_ns = now_ns
    if (not self.motion_valid or not math.isfinite(self.v_ego) or self.v_ego < 0 or
        not math.isfinite(self.a_ego) or (previous_ns is not None and now_ns < previous_ns)):
      self.clear_targets()
      return
    snapshot = self.read_targets(now_ns)
    if snapshot is None:
      self.clear_targets()
      return
    sample_ns = snapshot["sample_time_ns"]
    if sample_ns < self.sample_time_ns or (sample_ns == self.sample_time_ns and
        (self.snapshot is None or snapshot["points"] != self.snapshot["points"])):
      self.clear_targets()
      return
    if sample_ns > self.sample_time_ns:
      self.sample_time_ns = sample_ns
      self.travelled_m = self.v_ego * ((now_ns - sample_ns) / 1e9)
    else:
      # 重复快照只能缩短期限；不能重新扣传输延迟或重置已行驶距离。
      snapshot["valid_until_ns"] = min(snapshot["valid_until_ns"], self.snapshot["valid_until_ns"])
      if now_ns > snapshot["valid_until_ns"]:
        self.clear_targets()
        return
      self.travelled_m += self.v_ego * ((now_ns - previous_ns) / 1e9)
    self.snapshot = snapshot
    self.target_velocities = snapshot["points"]
    forward_points = self.target_velocities
    forward_distances = [max(0.0, point["distance_m"] - self.travelled_m) for point in forward_points]

    # find velocities that we are within the distance we need to adjust for
    valid_velocities = []
    for i in range(len(forward_points)):
      target_velocity = forward_points[i]
      tlat = target_velocity["latitude"]
      tlon = target_velocity["longitude"]
      tv = target_velocity["velocity"]
      if tv > self.v_ego:
        continue

      d = forward_distances[i]

      a_diff = (self.a_ego - TARGET_ACCEL)
      accel_t = abs(a_diff / TARGET_JERK)
      min_accel_v = calculate_velocity(accel_t, TARGET_JERK, self.a_ego, self.v_ego)

      max_d = 0
      if tv > min_accel_v:
        # calculate time needed based on target jerk
        a = 0.5 * TARGET_JERK
        b = self.a_ego
        c = self.v_ego - tv
        t_a = -1 * ((b**2 - 4 * a * c) ** 0.5 + b) / (2 * a)
        t_b = ((b**2 - 4 * a * c) ** 0.5 - b) / (2 * a)
        if not isinstance(t_a, complex) and t_a > 0:
          t = t_a
        else:
          t = t_b
        if isinstance(t, complex):
          continue

        max_d = max_d + calculate_distance(t, TARGET_JERK, self.a_ego, self.v_ego)
      else:
        t = accel_t
        max_d = calculate_distance(t, TARGET_JERK, self.a_ego, self.v_ego)

        # calculate additional time needed based on target accel
        t = abs((min_accel_v - tv) / TARGET_ACCEL)
        max_d += calculate_distance(t, 0, TARGET_ACCEL, min_accel_v)

      if d < max_d + tv * TARGET_OFFSET:
        valid_velocities.append((float(tv), tlat, tlon))

    # Find the smallest velocity we need to adjust for
    min_v = 100.0
    target_lat = 0.0
    target_lon = 0.0
    for tv, lat, lon in valid_velocities:
      if tv < min_v:
        min_v = tv
        target_lat = lat
        target_lon = lon

    if self.v_target < min_v and not (self.target_lat == 0 and self.target_lon == 0):
      for i in range(len(forward_points)):
        target_velocity = forward_points[i]
        tlat = target_velocity["latitude"]
        tlon = target_velocity["longitude"]
        tv = target_velocity["velocity"]
        if tv > self.v_ego:
          continue

        if tlat == self.target_lat and tlon == self.target_lon and tv == self.v_target:
          return

      # not found so let's reset
      self.v_target = 0.0
      self.target_lat = 0.0
      self.target_lon = 0.0

    self.v_target = min_v
    self.target_lat = target_lat
    self.target_lon = target_lon

  def _update_state_machine(self) -> tuple[bool, bool]:
    # ENABLED, TURNING
    if self.state != MapState.disabled:
      if not self.long_enabled or not self.enabled:
        self.state = MapState.disabled
      elif self.long_override:
        self.state = MapState.overriding

      else:
        # ENABLED
        if self.state == MapState.enabled:
          if self.v_cruise > self.v_target != 0:
            self.state = MapState.turning

        # TURNING
        elif self.state == MapState.turning:
          if self.v_cruise <= self.v_target or self.v_target == 0:
            self.state = MapState.enabled

        # OVERRIDING
        elif self.state == MapState.overriding:
          if not self.long_override:
            if self.v_cruise > self.v_target != 0:
              self.state = MapState.turning
            else:
              self.state = MapState.enabled

    # DISABLED
    elif self.state == MapState.disabled:
      if self.long_enabled and self.enabled:
        if self.long_override:
          self.state = MapState.overriding
        else:
          self.state = MapState.enabled

    enabled = self.state in ENABLED_STATES
    active = self.state in ACTIVE_STATES

    return enabled, active

  def update(self, long_enabled: bool, long_override: bool, v_ego, a_ego, v_cruise, *, motion_valid: bool = True) -> None:
    self.motion_valid = motion_valid
    self.long_enabled = long_enabled
    self.long_override = long_override
    self.v_ego = v_ego
    self.a_ego = a_ego
    self.v_cruise = v_cruise

    self.update_params()
    self.update_calculations()

    self.is_enabled, self.is_active = self._update_state_machine()

    self.output_v_target = self.get_v_target_from_control()
    self.output_a_target = self.get_a_target_from_control()

    self.frame += 1
