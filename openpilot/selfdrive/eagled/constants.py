"""Tunable constants for the 5Hz in-lane avoidance planner and perception.

Everything that shapes the lane offset lives here so Params can override the
limits without touching planner logic.
"""

from openpilot.common.model_geometry import CAMERA_TO_FRONT_DEFAULT

# Geometry
L_LOOKAHEAD = 35.0   # m, preview distance for the lane-offset closed loop and the cap's lane-width scan
EGO_HALF_WIDTH = 0.9  # 自车半宽 m(Toyota B 级 SUV 量级)

# 变道放行的时间投影(carrotpilot 语义):侧车 LEAD_TIME 秒后位置 vs 我们
# EGO_TIME 秒后位置,对方多跑 1 秒是安全裕量。"远而快"的侧车因此放行;
# 速度未知(视觉独有)与近区目标不放宽。
LANE_CHANGE_LEAD_TIME_S = 4.0
LANE_CHANGE_EGO_TIME_S = 3.0
LANE_CHANGE_NEAR_D = 6.0       # 近区硬拦 m:贴身目标无论投影如何都不清空

# --- C2: 车道相对分类 -----------------------------------------------------------
# modelV2 约定(三源验证: ldw.py / relc.py / radard.py): y 右正,雷达 yRel 左正;
# laneLines[1]=本道左边界, [2]=本道右边界; roadEdges[0]=左沿, [1]=右沿。
LANE_IDX_LEFT = 1    # laneLines 索引: 本道左边界
LANE_IDX_RIGHT = 2  # laneLines 索引: 本道右边界
LANE_IDX_OUTER_LEFT = 0    # laneLines 索引: 左邻道外侧线(变道目标车道的外边界)
LANE_IDX_OUTER_RIGHT = 3   # laneLines 索引: 右邻道外侧线

# 类别半宽(m): 侵入判据用车身边缘,不是中心 —— "车屁股侵入车道"的语义来源。
CLASS_HALF_WIDTHS_M = {
  "person": 0.30, "rider": 0.30, "bicycle": 0.30, "motorcycle": 0.30,
  "tricycle": 0.60, "car": 0.90, "bus": 1.30, "truck": 1.30,
}
DEFAULT_HALF_WIDTH_M = 0.50   # 无类别(纯雷达未关联)目标的保守半宽


def class_half_width(cls) -> float:
  """类别 -> 车身半宽(m),未知/缺失按 DEFAULT_HALF_WIDTH_M。唯一实现,压力、变道清空、debug 遥测都走这里。"""
  return CLASS_HALF_WIDTHS_M.get(cls, DEFAULT_HALF_WIDTH_M)


# --- C7: 感知置信门控 -------------------------------------------------------------
# StarPilot lane_centering.py 同款参考值: 车道线概率够高、方差够低才可信。
LANE_PROB_MIN = 0.6    # laneLineProbs 门槛(StarPilot _MIN_LANE_PROB)
LANE_STD_MAX = 0.3     # laneLineStds 上限(StarPilot _MAX_LANE_STD)


# --- 车道内避让(CONTEXT.md):压力 -> 车道内偏移 ---------------------------------------
# 以下均为 Params 可覆盖的初值,路测后在 LANLink 调。
LANE_EDGE_MARGIN = 0.15        # 贴线余量 m:达到贴线上限时车身边缘距车道线保留的距离
TRIGGER_LINE_DISTANCE_VRU = 1.0          # 弱势交通参与者触发线距 m:线距小于此值开始产生压力
TRIGGER_LINE_DISTANCE_VEHICLE = 0.5      # 机动车(含未知类别)触发线距 m
TRIGGER_RANGE_MAX = 60.0       # 距离上限 m:更远的目标不计入
V_EGO_MIN_KPH = 15.0           # 车速下限 km/h
OFFSET_RATE = 0.3              # 车道内偏移的横向速率限制 m/s
TIME_WINDOW_S = 4.0            # 到达时间窗口 s:到达时间 = dRel / 接近速度,超过不计入
HOLD_MAX_S = 5.0               # 并行保持最长时限 s:目标离开视野后最多再保留这么久
VISION_TARGET_SPEED = 5.0      # 纯视觉目标假设对地纵向速度 m/s(速度未知时保守推算)
APPROACH_SPEED_MIN = 1.0       # 纯视觉目标接近速度下限 m/s
HOLD_PASS_MARGIN = 10.0        # 并行保持的车长余量 m:dRel 低于 -此值才算自车已完全超过目标(目标车长 + 自车车长)


# Camera -> car-frame projection (spec §4). Initial mount values; the P0
# calibration (shadow harness, spec §4) refines them.
CAMERA_HEIGHT = 1.2    # m, wide camera above the ground (windshield mount)
CAMERA_PITCH = 0.0     # rad, camera pitch, positive = tilted down
CAMERA_YAW = 0.0       # rad, camera yaw, positive = looking left
# The windshield camera sits behind the front bumper — the radar's dRel origin
# (radar_interface.py fills dRel "from front of car"). A camera-frame ground
# point is therefore FARTHER than the radar-frame distance, so this offset is
# subtracted when aligning projected dRel to radar dRel.
# 出厂默认/初值（票 #6 降级）：数值唯一来源在 model_geometry；运行时值一律经
# model_geometry.read_camera_to_front（Params 键 CameraToFront）每帧读取，别处不得直读。
CAMERA_TO_FRONT = CAMERA_TO_FRONT_DEFAULT

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


DT_5HZ = 0.2


# --- Params 可覆盖的调参表 ---------------------------------------------------------
# (param 键 -> (常量名, 编译期默认, 钳制范围))。eagled 1Hz 刷新时按 Params
# 重绑本模块属性:键缺失/空值恢复编译期默认。lane_offset 等
# 纯函数通过 ``C.*`` 在调用时取值,零签名改动即可吃到覆盖。lanlink 设置面板
# 里这些键的 stepper 直接驱动本机制。
_PARAM_OVERRIDABLE: dict[str, tuple[str, float, float, float]] = {
  "AvoidanceEgoHalfWidth":  ("EGO_HALF_WIDTH", EGO_HALF_WIDTH, 0.5, 1.5),
  "AvoidanceLaneProbMin":   ("LANE_PROB_MIN", LANE_PROB_MIN, 0.3, 0.95),
  "AvoidanceLaneStdMax":    ("LANE_STD_MAX", LANE_STD_MAX, 0.05, 1.0),
  "LaneChangeNearZone":     ("LANE_CHANGE_NEAR_D", LANE_CHANGE_NEAR_D, 0.0, 20.0),
  "AvoidanceLaneEdgeMargin":   ("LANE_EDGE_MARGIN", LANE_EDGE_MARGIN, 0.05, 0.5),
  "AvoidanceVruTriggerLineDistance":    ("TRIGGER_LINE_DISTANCE_VRU", TRIGGER_LINE_DISTANCE_VRU, 0.2, 3.0),
  "AvoidanceVehicleTriggerLineDistance": ("TRIGGER_LINE_DISTANCE_VEHICLE", TRIGGER_LINE_DISTANCE_VEHICLE, 0.1, 2.0),
  "AvoidanceMaxRange":         ("TRIGGER_RANGE_MAX", TRIGGER_RANGE_MAX, 20.0, 100.0),
  "AvoidanceMinSpeedKph":      ("V_EGO_MIN_KPH", V_EGO_MIN_KPH, 0.0, 60.0),
  "AvoidanceLateralRate":      ("OFFSET_RATE", OFFSET_RATE, 0.05, 1.0),
  "AvoidanceTimeWindow":       ("TIME_WINDOW_S", TIME_WINDOW_S, 1.0, 10.0),
  "AvoidanceHoldMax":          ("HOLD_MAX_S", HOLD_MAX_S, 0.0, 15.0),
  "AvoidanceVisionTargetSpeed": ("VISION_TARGET_SPEED", VISION_TARGET_SPEED, 0.0, 15.0),
}


def apply_param_overrides(params) -> None:
  """按 Params 重绑可覆盖常量(缺键恢复默认,带钳制)。

  测试安全:注入的 _FakeParams.get 返回 None -> 全部恢复编译期默认,
  单测的确定性不受 Params 环境影响。
  """
  for key, (name, default, lo, hi) in _PARAM_OVERRIDABLE.items():
    try:
      raw = params.get(key)
    except Exception:
      raw = None
    value = default if raw in (None, b"", "") else float(raw)
    globals()[name] = min(max(value, lo), hi)


# 对地速度低于此值判为静止(m/s)。静止雷达目标必须有视觉关联确认才保留:
# 护栏、桥墩的对地速度是 0,但抛锚车、路口停车也是 0。单纯的速度门会把静止
# 车辆一并滤掉,而静止车辆是需要避让的真实障碍;视觉能区分二者(会把停着的
# 车报成 car,不会把护栏报成 car/person)。
STATIC_SPEED_THRESH = 1.0

# 车速上限 m/s(下限见 V_EGO_MIN_KPH)
V_EGO_MAX = 33.0

# YOLO classes treated as vulnerable road users (wider trigger line distance)
VRU_CLASSES = frozenset({"person", "rider", "bicycle", "motorcycle", "tricycle"})
