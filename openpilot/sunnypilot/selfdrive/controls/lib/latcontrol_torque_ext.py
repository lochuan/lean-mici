"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from openpilot.sunnypilot.selfdrive.locationd.speed_dep_config import get_speed_dep_config
from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_jerk_aware import LatControlTorqueJerkAware
from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_ext_override import LatControlTorqueExtOverride


class LatControlTorqueExt(LatControlTorqueJerkAware, LatControlTorqueExtOverride):
  def __init__(self, lac_torque, CP, CP_SP, CI):
    LatControlTorqueJerkAware.__init__(self, lac_torque, CP, CP_SP, CI)
    LatControlTorqueExtOverride.__init__(self, CP)

  def update(self, CS, VM, pid, params, ff, pid_log, setpoint, measurement, calibrated_pose, roll_compensation,
             desired_lateral_accel, actual_lateral_accel, lateral_accel_deadzone, gravity_adjusted_lateral_accel,
             desired_curvature, actual_curvature, steer_limited_by_safety, output_torque):
    # Store vEgo for update_override_torque_params (which runs before this, next frame)
    self._last_vego = CS.vEgo
    self._ff = ff
    self._pid = pid
    self._pid_log = pid_log
    self._setpoint = setpoint
    self._measurement = measurement
    self._roll_compensation = roll_compensation
    self._lateral_accel_deadzone = lateral_accel_deadzone
    self._desired_lateral_accel = desired_lateral_accel
    self._actual_lateral_accel = actual_lateral_accel
    self._desired_curvature = desired_curvature
    self._actual_curvature = actual_curvature
    self._gravity_adjusted_lateral_accel = gravity_adjusted_lateral_accel
    self._steer_limited_by_safety = steer_limited_by_safety
    self._output_torque = output_torque

    self.update_calculations(CS, VM, desired_lateral_accel)
    self.update_jerk_aware_torque_control(CS, roll_compensation, gravity_adjusted_lateral_accel)

    return self._pid_log, self._output_torque

  def update_speed_dep_torque(self, tp):
    """Apply speed-dependent learned values from torqued.
    Uses learned values for valid bins. For invalid bins, falls back to
    configured seed values if available for this car, otherwise global filtered."""
    speed_bp = list(tp.speedBinCenters)
    factors = list(tp.speedBinLatAccelFactors)
    frictions = list(tp.speedBinFrictions)
    valid_bp = list(tp.speedBinValid)
    if not speed_bp or not (len(factors) == len(frictions) == len(valid_bp) == len(speed_bp)):
      self._speed_dep_active = False
      return

    cfg = get_speed_dep_config().get(self.CP.carFingerprint, {})
    seed_lafs = cfg.get('laf_bp')
    seed_frictions = cfg.get('friction_bp')
    if (seed_lafs and seed_frictions and
        len(seed_lafs) == len(speed_bp) and len(seed_frictions) == len(speed_bp)):
      fallback_factors = seed_lafs
      fallback_frictions = seed_frictions
    else:
      fallback_factors = [tp.latAccelFactorFiltered] * len(speed_bp)
      fallback_frictions = [tp.frictionCoefficientFiltered] * len(speed_bp)

    self._speed_dep_active = True
    self._speed_dep_speed_bp = speed_bp
    self._speed_dep_lat_accel_factor_bp = [factors[i] if valid_bp[i] else fallback_factors[i] for i in range(len(speed_bp))]
    self._speed_dep_friction_bp = [frictions[i] if valid_bp[i] else fallback_frictions[i] for i in range(len(speed_bp))]
