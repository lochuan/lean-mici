# system/lanlinkd/avoidanced.py
"""eagleDebug 快照线程：缓存最新一帧避让监测数据，线程安全。

独立线程跑 SubMaster，锁下缓存最新快照。eagleDebug 是 5Hz 在线观测
流（不落盘），给 lanlink 鸟瞰图和标定工具看——每帧构建 targets 列表
dict，序列化在收帧时一次完成。快照含雷达 CAN 错误标志（canError /
radarUnavailable，来自 radarTracks.errors，随 debug 透传），所以前端
不再需要独立的 /api/radar 端点。

staleness 用本地接收时刻（time.monotonic）判断：
eagleDebug 是 5Hz，STALE_AFTER_MS 取 1s（5 帧没新数据即视为停更）。

标定状态单独订阅 extrinsicsCalibration 透传：avoidanced 在相机未标定时整体
关掉视觉路径（地平面投影的 dRel 对 pitch 的敏感度在 40m 处是 0.5° → 41%，
未标定的 pitch 会直接生成虚假偏移），前端否则只会看到 nVision 恒为 0 而没有
任何解释。EagleDebug 的 capnp 结构里没有降级原因字段，而 openpilot/cereal
在 release_lib 的 NATIVE_INPUT_PATHS 里 —— 加一个字段就要设备全量重建 30-60
分钟。lanlinkd 自己订阅是等价且免费的。
"""
import threading
import time

from openpilot.cereal import messaging
from openpilot.common.swaglog import cloudlog
from openpilot.common.model_geometry import read_camera_to_front
from openpilot.system.lanlinkd import lanes as lanes_mod

STALE_AFTER_MS = 1000


def _target(t) -> dict:
  return {
    "dRel": float(t.dRel),
    "yRel": float(t.yRel),
    "vRel": float(t.vRel),
    "cls": str(t.cls),
    "conf": float(t.conf),
    "weight": float(t.weight),
    "matched": bool(t.matched),
    "inGate": bool(t.inGate),
    "vision": bool(t.vision),
    "pairId": int(t.pairId),
    "lane": int(t.lane),
  }


def _calibration(msg, valid: bool) -> dict:
  """extrinsicsCalibration -> 前端要的标定摘要。

  ``visionGated`` 是给前端解释 ``nVision == 0`` 用的：avoidanced 只在标定
  有效时才跑视觉路径，判定与 projection.geometry_from_calibration 一致
  （必须 valid、calStatus == "calibrated"、且 rpyCalib 长度为 3 —— 一个
  空的 rpyCalib 配 "calibrated" 不能当成零角度）。
  """
  status = str(getattr(msg, "calStatus", "unknown"))
  rpy = list(getattr(msg, "rpyCalib", []) or [])
  cal_valid = bool(valid) and status == "calibrated" and len(rpy) == 3
  return {
    "calStatus": status,
    "calPerc": int(getattr(msg, "calPerc", 0) or 0),
    "calValid": cal_valid,
    "visionGated": not cal_valid,
  }


class AvoidanceCache:
  def __init__(self, params):
    self._lock = threading.Lock()
    self._snapshot: dict = {"stale": True}
    self._recv_ms: float = 0.0
    self._params = params
    # 车道几何（modelV2 → lanes.py）：请求时取帧，无后台循环。
    # snapshot() 只在 API handler 线程调用，LaneCache 的 SubMaster 惰性
    # 创建、只被该线程触碰，与 run() 线程无共享。
    # camera_to_front：安装偏移每次快照经唯一读点取值注入（票 #6，保存即生效）。
    self._lane_cache = lanes_mod.LaneCache()
    # 标定状态与 eagleDebug 分开缓存：两者频率不同（100Hz vs 5Hz），
    # 且标定即使停更也仍然是有效信息，不该被 debug 的 staleness 抹掉。
    self._cal: dict = {"calStatus": "unknown", "calPerc": 0, "calValid": False, "visionGated": True}

  def run(self, exit_event: threading.Event) -> None:
    try:
      sm = messaging.SubMaster(['eagleDebug', 'extrinsicsCalibration'])
    except Exception:
      cloudlog.exception("lanlink avoidanced: SubMaster init failed")
      return
    while not exit_event.is_set():
      sm.update(1000)
      if sm.updated['extrinsicsCalibration']:
        with self._lock:
          self._cal = _calibration(sm['extrinsicsCalibration'], sm.valid['extrinsicsCalibration'])
      if not sm.updated['eagleDebug']:
        continue
      dbg = sm['eagleDebug']
      try:
        snap = {
          "stale": False,
          "logMonoTime": int(sm.logMonoTime['eagleDebug']),
          "valid": bool(dbg.valid),
          "active": bool(dbg.active),
          "direction": int(dbg.direction),
          "yDes": float(dbg.yDes),
          "bias": float(dbg.bias),
          "maxOffset": float(dbg.maxOffset),
          "bsmLeft": bool(dbg.bsmLeft),
          "bsmRight": bool(dbg.bsmRight),
          "vEgo": float(dbg.vEgo),
          "nRadar": int(dbg.nRadar),
          "nVision": int(dbg.nVision),
          "nAssociated": int(dbg.nAssociated),
          "edgeClearance": float(dbg.edgeClearance),
          "canError": bool(dbg.canError),
          "radarUnavailable": bool(dbg.radarUnavailable),
          "laneLeftValid": bool(dbg.laneLeftValid),
          "laneRightValid": bool(dbg.laneRightValid),
          "budgetLeft": float(dbg.budgetLeft),
          "budgetRight": float(dbg.budgetRight),
          "changeClearLeft": bool(dbg.changeClearLeft),
          "changeClearRight": bool(dbg.changeClearRight),
          "targets": [_target(t) for t in dbg.targets],
        }
      except Exception:
        cloudlog.exception("lanlink avoidanced: snapshot build failed")
        continue
      with self._lock:
        self._snapshot = snap
        self._recv_ms = time.monotonic() * 1000.0

  def snapshot(self) -> dict:
    with self._lock:
      snap = dict(self._snapshot)
      recv_ms = self._recv_ms
      cal = dict(self._cal)
    if not snap.get("stale") and (time.monotonic() * 1000.0 - recv_ms) > STALE_AFTER_MS:
      snap = {"stale": True}
    # 标定状态即使 debug 停更也要带上：前端用它区分「避让没在跑」和
    # 「避让在跑但视觉被标定门关掉了」。
    snap.update(cal)
    # 车道几何：modelV2 停更/无帧时为 None，前端不画车道层。
    snap["lanes"] = self._lane_cache.snapshot(camera_to_front=read_camera_to_front(self._params))
    return snap
