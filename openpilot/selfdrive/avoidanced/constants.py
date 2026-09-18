"""Tunable constants for the 5Hz lateral avoidance planner.

Values mirror the design doc (``2026-09-18-yolo-avoidanced-design.md`` §3) and the
Task 5 brief. Everything that shapes the bias lives here so Params can override
the limits without touching planner logic.
"""

# Geometry / gate
L_LOOKAHEAD = 35.0   # m, preview distance used for the curvature bias
D_MAX = 50.0         # m, proximity ramp far distance
Y_GATE = 2.5         # m, |yRel| gate: only same-lane-ish targets trigger
D_GATE = 40.0        # m, near-trigger distance (spec §3)

# Offset limits (m)
MAX_OFFSET_FREE = 0.35  # no adjacent vehicle
MAX_OFFSET_BSM = 0.12   # vehicle on the opposite blind spot
EDGE_CLEAR_MIN = 0.6    # m, minimum road-edge clearance

# Target weighting: y_des = -sign(yRel) * min(max_offset, K * w_cls * proximity)
K_GAIN = 0.5
VRU_WEIGHT = 1.0      # person / bicycle / motorcycle
VEHICLE_WEIGHT = 0.6  # car

# Speed envelope (m/s) — spec §3 suggests 30-120 kph
V_EGO_MIN = 8.0
V_EGO_MAX = 33.0

# Temporal filtering / hysteresis (s)
DT_5HZ = 0.2
LOWPASS_TAU_S = 0.5
ENTER_HOLD_S = 0.5
EXIT_HOLD_S = 1.0

# YOLO classes treated as vulnerable road users (higher avoidance weight)
VRU_CLASSES = frozenset({"person", "bicycle", "motorcycle"})

# Camera -> car-frame projection (spec §4). Initial mount values; the P0
# calibration (shadow harness, spec §4) refines them.
CAMERA_HEIGHT = 1.2    # m, wide camera above the ground (windshield mount)
CAMERA_PITCH = 0.0     # rad, camera pitch, positive = tilted down
CAMERA_YAW = 0.0       # rad, camera yaw, positive = looking left
# The windshield camera sits behind the front bumper — the radar's dRel origin
# (radar_interface.py fills dRel "from front of car"). A camera-frame ground
# point is therefore FARTHER than the radar-frame distance, so this offset is
# subtracted when aligning projected dRel to radar dRel.
CAMERA_TO_FRONT = 1.5  # m, windshield camera behind the front bumper
