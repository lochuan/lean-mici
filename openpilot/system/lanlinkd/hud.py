# system/lanlinkd/hud.py
"""驾驶辅助 HUD 帧：eagleState + modelV2 组成一帧，经 SSE 推给手机。

车机侧只转发权威事实、不复算（ADR 0001 / 0002）：目标与视觉状态来自
``eagleState``，车道几何来自 ``modelV2``（经 ``lanes`` 的车体系换算，沿用
安装偏移的唯一读点）。帧内所有量统一车体系：x 前向、前保险杠为原点，y 左正，
米制；缺失即为 ``None``，不用 0 / 999 / 空串冒充。

推送节奏：``eagleState`` 每到一帧推一帧（约 5Hz）；静默 1 秒发一次心跳；
``eagleState`` 超龄或无效时推过期帧（之后静默期只发心跳，手机端以最后一帧为准）。
订阅只在有客户端连接时才建立（见 :meth:`HudStream.events`）。
"""
from __future__ import annotations

import json
import time

from openpilot.cereal import messaging
from openpilot.common.model_geometry import read_camera_to_front
from openpilot.common.stream_gate import StreamStatus, stream_status
from openpilot.selfdrive.eagled.constants import STATIC_SPEED_THRESH
from openpilot.system.lanlinkd import lanes as lanes_mod

HEARTBEAT_TIMEOUT_MS = 1000
VISION_CLASSES = ("person", "bicycle", "motorcycle", "car")
UNCLASSIFIED = "unclassified"
STALE_FRAME = {"t": None, "stale": True, "vEgo": None, "lanes": None, "vehicles": None, "sensors": None}


def _r(value, digits=2):
  return None if value is None else round(float(value), digits)


def _lanes(lane_geo: dict | None, left_trusted: bool, right_trusted: bool) -> dict | None:
  if lane_geo is None:
    return None
  geo = lane_geo["corrected"]
  return {
    "x": lane_geo["x"],
    "lines": [None if line is None else {"y": [_r(y) for y in line["y"]], "conf": _r(line["prob"])}
              for line in geo["laneLines"]],
    "edges": [None if edge is None else {"y": [_r(y) for y in edge["y"]]} for edge in geo["roadEdges"]],
    "egoLeftTrusted": left_trusted,
    "egoRightTrusted": right_trusted,
  }


def _vehicles(targets, v_ego: float, lane_trusted: bool) -> list[dict]:
  """雷达与视觉已配对的目标合并为一项（位置取雷达、类别取视觉）；
  雷达独有的对地静止点丢弃，雷达独有的动态点标 unclassified。"""
  vision_cls = {t.pairId: str(t.cls) for t in targets if t.vision and t.pairId}
  out = []
  for t in targets:
    if t.vision and t.pairId:
      continue  # 配对的视觉行并入雷达行
    paired = bool(t.pairId)
    if not t.vision and not paired and abs(t.vRel + v_ego) < STATIC_SPEED_THRESH:
      continue
    cls = vision_cls.get(t.pairId, str(t.cls)) if paired else str(t.cls)
    out.append({
      "dRel": _r(t.dRel),
      "yRel": _r(t.yRel),
      "cls": cls if cls in VISION_CLASSES else UNCLASSIFIED,
      "lane": int(t.lane) if lane_trusted else None,
    })
  return out


def build_frame(state, log_mono_time: int, lane_geo: dict | None) -> dict:
  lane_trusted = bool(state.laneLeftValid and state.laneRightValid)
  return {
    "t": int(log_mono_time),
    "stale": False,
    "vEgo": _r(state.vEgo),
    "lanes": _lanes(lane_geo, bool(state.laneLeftValid), bool(state.laneRightValid)),
    "vehicles": _vehicles(state.targets, float(state.vEgo), lane_trusted),
    "sensors": {
      "radar": "canError" if state.canError else "unavailable" if state.radarUnavailable else "ok",
      "vision": str(state.visionState),
    },
  }


def sse(event: str, data: dict) -> str:
  return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


class HudStream:
  def __init__(self, params, sm_factory=None, clock=time.monotonic):
    self._params = params
    self._sm_factory = sm_factory or (lambda: messaging.SubMaster(['eagleState', 'modelV2'], poll='eagleState'))
    self._clock = clock

  def _lane_geo(self, sm) -> dict | None:
    return lanes_mod.lane_snapshot(sm['modelV2'], sm.recv_time['modelV2'], self._clock(),
                                   camera_to_front=read_camera_to_front(self._params),
                                   valid=bool(sm.valid['modelV2'])) if sm.seen['modelV2'] else None

  def events(self):
    """阻塞式事件迭代器：首次 next 才建立订阅，丢弃迭代器即退订。"""
    sm = self._sm_factory()
    last_stale = None
    while True:
      sm.update(HEARTBEAT_TIMEOUT_MS)
      age = (self._clock() - sm.recv_time['eagleState']) if sm.seen['eagleState'] else None
      fresh = stream_status("eagleState", age, valid=bool(sm.valid['eagleState'])) is StreamStatus.FRESH
      if sm.updated['eagleState'] and fresh:
        last_stale = False
        yield sse("frame", build_frame(sm['eagleState'], sm.logMonoTime['eagleState'], self._lane_geo(sm)))
      elif not fresh and last_stale is not True:
        last_stale = True
        yield sse("frame", STALE_FRAME)
      else:
        yield sse("heartbeat", {})
