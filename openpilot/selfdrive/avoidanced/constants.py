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
VRU_WEIGHT = 1.0      # person / rider / bicycle / motorcycle / tricycle
VEHICLE_WEIGHT = 0.6  # car / bus / truck

# Speed envelope (m/s) — spec §3 suggests 30-120 kph
V_EGO_MIN = 8.0
V_EGO_MAX = 33.0

# Temporal filtering / hysteresis (s)
DT_5HZ = 0.2
LOWPASS_TAU_S = 0.5
ENTER_HOLD_S = 0.5
EXIT_HOLD_S = 1.0

# Consumer-side freshness gate (s): lateralManeuverPlan older than this falls
# back to the model curvature even if the message valid flag is sticky-true.
AVOIDANCE_STALE_S = 1.0

# YOLO classes treated as vulnerable road users (higher avoidance weight)
VRU_CLASSES = frozenset({"person", "rider", "bicycle", "motorcycle", "tricycle"})

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

# ROI 模式。SQUASH 是历史行为:整帧压进 640x384。在 mici 宽相机(1344x760)上
# 它一行都没裁(crop_h = min(760, 806) = 760),整帧被压 2.1x/1.98x 且带 6%
# 各向异性,40m 行人只剩 9 像素 —— 这是远距 VRU 置信度低的根因。
# NATIVE 原生 1:1 裁 640x384:Y_GATE=2.5m 在 40m 处只有 ±27px,原生裁剪的
# ±320px 完整覆盖决策区域直到 3.32m 以内,所以像素翻倍而有效 FOV 无损。
# 默认保持 SQUASH:尺度分布偏移对 BDD 训练的模型是好是坏尚未上车实测。
ROI_MODE_SQUASH = "squash"
ROI_MODE_NATIVE = "native"
ROI_MODE = ROI_MODE_SQUASH

# NATIVE 模式下 ROI 顶边在地平线上方留多少行。64 行使 ROI 落在 [316, 700]:
# 远端覆盖无穷远,近端接地距离 1.59m,40m 卡车顶(行 350)在窗口内。
ROI_HORIZON_MARGIN = 64

# 框高测距用的假设物体高度(m)。40m 处 1px 框高误差约 11% 距离误差,叠加成人
# 身高方差 ±12%,合计 15-20% —— 对比地平面投影 0.5deg pitch 就 41%,而且框高
# 测距完全不依赖 pitch。
CLASS_HEIGHTS_M = {
  "person": 1.70, "rider": 1.70, "bicycle": 1.70, "motorcycle": 1.70,
  "tricycle": 1.60, "car": 1.50, "bus": 3.20, "truck": 3.20,
}

# 框底距图像下边界小于这个像素数即视为截断。截断框的 y2 不是真实接地点,
# 地平面投影会系统性偏近,框高也被截短导致距离偏远。
TRUNCATION_MARGIN_PX = 4.0

# 关联门限。方位角是单目唯一可靠的量(只依赖内参和 yaw/roll,与 pitch 无关),
# 而旧的笛卡尔门 dx<=2m 恰好建在最不可靠的轴上:40m 处 0.5deg pitch 误差就造成
# 16m 的 dx 偏差,远处几乎永不匹配。
# 0.035 rad ~= 2.0deg,对应 40m 处 1.40m 横向、10m 处 0.35m。
ASSOC_MAX_DBEARING = 0.035
# 二级校验:方位角相同但距离差极大的两个目标不应配上。用框高测距的距离与雷达
# 距离在横向上的差值做门限。
ASSOC_MAX_DY_M = 2.0
