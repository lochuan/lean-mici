"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Speed-dependent torque parameters — per-car speed-binned config.

Each entry maps a car fingerprint to:
  speed_bp    — speed breakpoints in m/s (bin centers)
  laf_bp      — latAccelFactor at each speed
  friction_bp — friction coefficient at each speed

torqued runs independent SVD fits per speed bin. laf_bp/friction_bp provide
starting values and define the sanity bounds of each bin.

Cars without an entry here seed all bins with their global offline LAF and
friction values when speed-dependent learning is enabled.

Example (Mazda CX-5 2022, learned from device data):
  "MAZDA_CX5_2022": {
    "speed_bp":    [6.5, 10.0, 15.0, 21.0, 26.5, 32.0, 37.5],
    "laf_bp":      [2.390, 2.522, 2.710, 2.386, 2.281, 2.220, 2.209],
    "friction_bp": [0.177, 0.158, 0.131, 0.118, 0.113, 0.109, 0.108],
  },
"""

SPEED_DEP_CONFIG: dict[str, dict[str, list[float]]] = {}


def get_speed_dep_config() -> dict[str, dict[str, list[float]]]:
  return SPEED_DEP_CONFIG
