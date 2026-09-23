"""Tunable constants for the 5Hz lateral avoidance planner.

Values mirror the design doc (``2026-09-18-yolo-eagled-design.md`` §3) and the
Task 5 brief. Everything that shapes the bias lives here so Params can override
the limits without touching planner logic.
"""

# Geometry / gate
L_LOOKAHEAD = 35.0   # m, preview distance used for the curvature bias
D_MAX = 50.0         # m, proximity ramp far distance
Y_GATE = 2.5         # m, |yRel| gate: only same-lane-ish targets trigger
D_GATE = 40.0        # m, near-trigger distance (spec §3)
# 本车道半宽(m)。|yRel| 小于此值的目标不作为避让目标:0.35m 的偏置绕不开
# 本车道内的障碍(比如抛锚车),只是白占横向空间,而且 _sign(0.0)=1 会让正
# 前方目标固定往右让 —— 方向是任意的。留给驾驶员接管。
OWN_LANE_HALF_WIDTH = 1.2

# Offset limits (m)
MAX_OFFSET_FREE = 0.35  # no adjacent vehicle
MAX_OFFSET_BSM = 0.12   # vehicle on the opposite blind spot
EDGE_CLEAR_MIN = 0.6    # m, minimum road-edge clearance

# Target weighting: y_des = -sign(yRel) * min(max_offset, K * w_cls * proximity)
K_GAIN = 0.5
VRU_WEIGHT = 1.0      # person / rider / bicycle / motorcycle / tricycle
VEHICLE_WEIGHT = 0.6  # car / bus / truck

# 对地速度低于此值判为静止(m/s)。静止雷达目标必须有视觉关联确认才保留:
# 护栏、桥墩的对地速度是 0,但抛锚车、路口停车也是 0。单纯的速度门会把静止
# 车辆一并滤掉,而静止车辆是需要避让的真实障碍;视觉能区分二者(会把停着的
# 车报成 car,不会把护栏报成 car/person)。
STATIC_SPEED_THRESH = 1.0

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


def class_weight(cls) -> float:
  """类别 -> 避让权重(VRU > vehicle),未知/缺失类别按 vehicle。

  唯一实现,不许再内联 ``VRU_WEIGHT if cls in VRU_CLASSES else VEHICLE_WEIGHT``:
  planner、projection 和 debug 遥测都必须走这里 —— 各写一份的拷贝曾让 debug
  上报的权重和 planner 实际使用的不一致(本分支修掉过的遥测 bug)。
  """
  return VRU_WEIGHT if cls in VRU_CLASSES else VEHICLE_WEIGHT


# --- C2: 车道相对分类 -----------------------------------------------------------
# modelV2 约定(三源验证: ldw.py / relc.py / radard.py): y 右正,雷达 yRel 左正;
# laneLines[1]=本道左边界, [2]=本道右边界; roadEdges[0]=左沿, [1]=右沿。
LANE_IDX_LEFT = 1    # laneLines 索引: 本道左边界
LANE_IDX_RIGHT = 2  # laneLines 索引: 本道右边界

# 类别半宽(m): 侵入判据用车身边缘,不是中心 —— "车屁股侵入车道"的语义来源。
CLASS_HALF_WIDTHS_M = {
  "person": 0.30, "rider": 0.30, "bicycle": 0.30, "motorcycle": 0.30,
  "tricycle": 0.60, "car": 0.90, "bus": 1.30, "truck": 1.30,
}
DEFAULT_HALF_WIDTH_M = 0.50   # 无类别(纯雷达未关联)目标的保守半宽


def class_half_width(cls) -> float:
  """类别 -> 车身半宽(m),未知/缺失按 DEFAULT_HALF_WIDTH_M。与 class_weight 同理:
  唯一实现,分类、debug 遥测都走这里。"""
  return CLASS_HALF_WIDTHS_M.get(cls, DEFAULT_HALF_WIDTH_M)


# --- C7: 感知置信门控 -------------------------------------------------------------
# StarPilot lane_centering.py 同款参考值;每个依赖模型几何的门控同时检查该几何的
# 不确定度 —— 数据不确定就回退/禁止,而不是全信。

# lane-relative 分类置信: 本道两侧边界线都要概率够高、方差够低才可信。
LANE_PROB_MIN = 0.6    # laneLineProbs 门槛(StarPilot _MIN_LANE_PROB)
LANE_STD_MAX = 0.3     # laneLineStds 上限(StarPilot _MAX_LANE_STD)
# path-relative 回退置信: position.yStd 在目标前视点的插值上限
# (StarPilot _E2E_MAX_PATH_STD)。
PATH_STD_MAX = 0.35
# 路沿门控置信: roadEdgeStds 超过此值的边不参与净空判断(该侧视为无净空)。
EDGE_STD_MAX = 0.35


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
# 二级校验:方位角相同但距离差极大的两个目标不应配上。用框高测距的距离(已换算
# 到保险杠系)与雷达 dRel 的差值做门限 —— 这是距离差,不是横向差。
ASSOC_MAX_DRANGE_M = 2.0
