"""cruisebuttond 常数。数值依据与上车验证项见 spec §3/§11。"""

# 统一公式
FLOOR_KPH = 30.0          # 丰田 setSpeed 地板,上车验证项 4
MARGIN_KPH = 3.0          # 跟车余量
DEADBAND_KPH = 5.0        # 动手阈值
DEADBAND_S = 2.0          # 偏离持续
OFFSET_CAP_KPH = 10.0     # 偏移帽(双向)

# 节奏
DECEL_TAP_INTERVAL_S = 0.8   # 向下固定节奏
HOLD_RATE_KPH_S = 8.0        # 长按假设速率,上车验证项 5 标定
TAP_PRESS_MS = 180           # 短按按压时长(手册 ≤600ms=1 量子)
MIN_CMD_INTERVAL_S = 0.1    # 两拍最小间隔
MAX_PRESSES_PER_MIN = 60     # 按压节奏限幅
DEFAULT_ACCEL_MS2 = 0.8      # 滑条默认;范围 0.3–2.0

# 归属
ATTRIBUTION_WINDOW_S = 1.5  # 命令→回显匹配窗
TAP_HOLD_MS = 600            # 回显按压持续 <600ms=短按
UNEXPLAINED_FREEZE = 3      # 连续无法解释回显 → 冻结

# 量子
DEFAULT_QUANTUM_KPH = 1.0    # 仪表 mph 时运行时自适应

# 前车
LEAD_MAX_Y_ABS_M = 2.0      # 雷达点视为前车的横向窗口
LEAD_MIN_SPEED_MS = 0.5     # 起步判定:前车速度持续 >0.5 m/s
STANDSTILL_RESUME_DEBOUNCE_S = 1.0

# BLE GATT(spec §7,固件照此实现)
BLE_SERVICE_UUID = "9f2d0001-6b4e-4c2a-9d1f-3e8a5c7b2f01"
BLE_COMMAND_UUID = "9f2d0002-6b4e-4c2a-9d1f-3e8a5c7b2f02"
BLE_STATUS_UUID = "9f2d0003-6b4e-4c2a-9d1f-3e8a5c7b2f03"
# 命令帧 9B: [op][seq][button][mode][count][duration_ms u16 LE][interval_ms u16 LE]
OP_PRESS, OP_ABORT, OP_PING = 0, 1, 2
BUTTON_RES_UP, BUTTON_RES_DOWN = 0, 1
MODE_TAP, MODE_HOLD = 0, 1
# 状态帧 4B: [seq][state][pending][fw]
BT_IDLE, BT_EXECUTING, BT_FAULT = 0, 1, 2