"""HUD 帧契约：给定一组 cereal 消息，帧构建输出等于样例帧；SSE 在新帧 / 过期 / 心跳下推出正确内容。

样例帧 ``hud_frames/*.json`` 与手机端（chipmunk）各存一份结构相同的拷贝，契约漂移在两边测试里暴露。
"""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from openpilot.cereal import messaging
from openpilot.system.lanlinkd import hud, lanes

FRAMES = Path(__file__).parent / "hud_frames"
MONO_NS = 1_234_000_000


def _expected(name: str) -> dict:
  return json.loads((FRAMES / f"{name}.json").read_text())


def _line(y: float) -> NS:
  return NS(x=[0.0, 50.0], y=[y, y])


def _model_v2(prob=(0.1, 0.9, 0.9, 0.1)) -> NS:
  # 相机系 y 右正：本车道左边界 -1.75、右边界 +1.75
  return NS(laneLines=[_line(-5.25), _line(-1.75), _line(1.75), _line(5.25)],
            laneLineProbs=list(prob), laneLineStds=[0.5, 0.1, 0.1, 0.5],
            roadEdges=[_line(-7.0), None], roadEdgeStds=[0.05, 0.05],
            position=NS(x=[0.0, 50.0], y=[0.0, 0.0], yStd=[0.1, 0.1]))


def _lane_geo(model=None):
  return lanes.lane_snapshot(model or _model_v2(), recv_mono=100.0, now_mono=100.05, camera_to_front=1.5, valid=True)


def _state(targets, *, v_ego=20.0, left=True, right=True, vision="ok", can_error=False, radar_unavailable=False,
           clear=("clear", "clear"), offset=0.0, inactive_reason=""):
  msg = messaging.new_message('eagleState')
  st = msg.eagleState
  st.vEgo, st.laneLeftValid, st.laneRightValid = v_ego, left, right
  st.canError, st.radarUnavailable, st.visionState = can_error, radar_unavailable, vision
  st.changeClearLeftState, st.changeClearRightState = clear
  st.laneOffsetTarget, st.inactiveReason = offset, inactive_reason
  rows = st.init('targets', len(targets))
  for out, t in zip(rows, targets, strict=True):
    for k, v in t.items():
      setattr(out, k, v)
  return msg.as_reader().eagleState


def _maneuver(state="off", direction="none", left="none", right="none", hold="none"):
  """变道判定方（modeld）发布的两条消息：modelV2.meta 与 modelDataV2SP。"""
  meta = messaging.new_message('modelV2').modelV2.meta
  meta.laneChangeState, meta.laneChangeDirection = state, direction
  sp = messaging.new_message('modelDataV2SP').modelDataV2SP
  sp.leftLaneChangeBlock, sp.rightLaneChangeBlock, sp.laneChangeHoldReason = left, right, hold
  return meta, sp


RADAR_CAR = {"dRel": 30.0, "yRel": 0.0, "vRel": -5.0, "cls": "car", "matched": True, "vision": False, "pairId": 1, "lane": 0}
VISION_CAR = {"dRel": 30.4, "yRel": 0.1, "cls": "car", "conf": 0.9, "matched": True, "vision": True, "pairId": 1, "lane": 0}
RADAR_ONLY_MOVING = {"dRel": 40.0, "yRel": 3.5, "vRel": -2.0, "lane": -1}
RADAR_ONLY_STATIC = {"dRel": 25.0, "yRel": 5.0, "vRel": -20.0, "lane": 1}   # 对地静止：护栏之类，不画
VISION_PERSON = {"dRel": 15.0, "yRel": -3.5, "cls": "person", "conf": 0.8, "vision": True, "lane": 1}


ALL_TARGETS = [RADAR_CAR, VISION_CAR, RADAR_ONLY_MOVING, RADAR_ONLY_STATIC, VISION_PERSON]


def test_normal_driving_matches_sample():
  assert hud.build_frame(_state(ALL_TARGETS), MONO_NS, _lane_geo(), *_maneuver()) == _expected("normal")


def test_vision_degraded_matches_sample():
  state = _state([RADAR_ONLY_MOVING], left=False, vision="noModel", radar_unavailable=True, clear=("unknown", "unknown"))
  assert hud.build_frame(state, MONO_NS, _lane_geo(), *_maneuver()) == _expected("vision_degraded")


@pytest.mark.parametrize("sample, state_kwargs, maneuver_kwargs", [
  ("lane_change_blindspot", {"clear": ("blocked", "clear")}, {"state": "preLaneChange", "direction": "left", "left": "blindspot"}),
  ("lane_change_road_edge", {"clear": ("clear", "clear")}, {"state": "preLaneChange", "direction": "right", "right": "roadEdge"}),
  ("lane_change_target_not_clear", {"clear": ("clear", "blocked")}, {"state": "preLaneChange", "direction": "right", "right": "targetNotClear"}),
  ("lane_change_awaiting_confirm", {}, {"state": "preLaneChange", "direction": "left", "hold": "awaitingConfirm"}),
  ("lane_change_in_progress", {}, {"state": "laneChangeStarting", "direction": "left"}),
  ("avoidance_active_left", {"offset": 0.4}, {}),
  ("avoidance_blocked_steering", {"inactive_reason": "steering_pressed"}, {}),
])
def test_maneuver_samples(sample, state_kwargs, maneuver_kwargs):
  frame = hud.build_frame(_state(ALL_TARGETS, **state_kwargs), MONO_NS, _lane_geo(), *_maneuver(**maneuver_kwargs))
  assert frame == _expected(sample)


def test_maneuver_sections_are_null_when_model_messages_unseen():
  frame = hud.build_frame(_state([]), MONO_NS, None)
  assert frame["laneChange"] is None and frame["avoidance"]["offset"] == 0.0


def test_stale_matches_sample():
  assert hud.STALE_FRAME == _expected("stale")


def test_missing_lane_geometry_is_null_not_guessed():
  frame = hud.build_frame(_state([]), MONO_NS, None)
  assert frame["lanes"] is None


def test_unavailable_line_is_null():
  geo = _lane_geo()
  geo["corrected"]["laneLines"][0] = None
  frame = hud.build_frame(_state([]), MONO_NS, geo)
  assert frame["lanes"]["lines"][0] is None and frame["lanes"]["lines"][1] is not None


def test_radar_error_precedence():
  assert hud.build_frame(_state([], can_error=True, radar_unavailable=True), MONO_NS, None)["sensors"]["radar"] == "canError"


# --- SSE：新帧 / 过期 / 心跳 ----------------------------------------------------------

class FakeSM:
  """按脚本逐拍给出 (updated, state, valid, 距上次收帧秒数)；时钟由测试推进。"""

  def __init__(self, script, clock, with_maneuver=False):
    self._script, self._clock, self._with_maneuver = iter(script), clock, with_maneuver
    self.seen = {'eagleState': False, 'modelV2': False, 'modelDataV2SP': False}
    self.updated = {'eagleState': False}
    self.valid = {'eagleState': True, 'modelV2': True}
    self.recv_time = {'eagleState': 0.0, 'modelV2': 0.0}
    self.logMonoTime = {'eagleState': MONO_NS}
    self._state = None

  def update(self, timeout):
    updated, valid, advance_s = next(self._script)
    self._clock.now += advance_s
    self.updated['eagleState'] = updated
    self.valid['eagleState'] = valid
    if updated:
      self.seen['eagleState'] = True
      self.recv_time['eagleState'] = self._clock.now
      self._state = _state([RADAR_CAR])
      if self._with_maneuver:
        self.seen['modelV2'] = self.seen['modelDataV2SP'] = True
        self.recv_time['modelV2'] = self._clock.now

  def __getitem__(self, name):
    if name == 'modelV2':
      return messaging.new_message('modelV2').modelV2
    if name == 'modelDataV2SP':
      return _maneuver(left="blindspot")[1]
    return self._state


class Clock:
  now = 100.0

  def __call__(self):
    return self.now


def _events(script, with_maneuver=False):
  clock = Clock()
  stream = hud.HudStream(params=NS(get=lambda key: None), sm_factory=lambda: FakeSM(script, clock, with_maneuver), clock=clock)
  gen = stream.events()
  return [next(gen) for _ in script]


def _kind(event: str) -> str:
  return event.split("\n", maxsplit=1)[0].removeprefix("event: ")


def test_new_frame_is_pushed_per_eagle_state():
  events = _events([(True, True, 0.2), (True, True, 0.2)])
  assert [_kind(e) for e in events] == ["frame", "frame"]
  data = json.loads(events[0].split("\n")[1].removeprefix("data: "))
  assert data["stale"] is False and data["vEgo"] == 20.0 and data["lanes"] is None   # modelV2 未收到：空而非猜


def test_frame_carries_lane_change_once_modeld_messages_arrive():
  frame = json.loads(_events([(True, True, 0.2)], with_maneuver=True)[0].split("\n")[1].removeprefix("data: "))
  assert frame["laneChange"]["left"]["block"] == "blindspot" and frame["laneChange"]["state"] == "off"


def test_silence_sends_heartbeat():
  events = _events([(True, True, 0.2), (False, True, 0.8)])
  assert [_kind(e) for e in events] == ["frame", "heartbeat"]


def test_never_seen_pushes_stale_then_heartbeats():
  events = _events([(False, True, 1.0), (False, True, 1.0)])
  assert [_kind(e) for e in events] == ["frame", "heartbeat"]
  assert json.loads(events[0].split("\n")[1].removeprefix("data: "))["stale"] is True


def test_overage_pushes_stale_frame_once():
  events = _events([(True, True, 0.2), (False, True, 1.5), (False, True, 1.0)])
  assert [_kind(e) for e in events] == ["frame", "frame", "heartbeat"]
  assert json.loads(events[1].split("\n")[1].removeprefix("data: "))["stale"] is True


def test_invalid_envelope_pushes_stale_frame():
  events = _events([(True, False, 0.2)])
  assert json.loads(events[0].split("\n")[1].removeprefix("data: "))["stale"] is True


def test_recovery_pushes_fresh_frame_again():
  events = _events([(False, True, 1.0), (True, True, 0.2)])
  assert [json.loads(e.split("\n")[1].removeprefix("data: "))["stale"] for e in events] == [True, False]


def test_subscription_is_lazy():
  built = []
  stream = hud.HudStream(params=None, sm_factory=lambda: built.append(1))
  gen = stream.events()
  assert built == []   # 无客户端消费 = 不订阅
  del gen


def test_wire_format():
  assert hud.sse("heartbeat", {}) == "event: heartbeat\ndata: {}\n\n"


def test_real_msgq_roundtrip_with_default_subscription():
  """默认订阅（eagleState 驱动 + modelV2 取最新）走真实 msgq：发布 → 一帧 frame。"""
  import threading
  import time
  pm = messaging.PubMaster(['eagleState'])
  stop = threading.Event()

  def publish():
    while not stop.is_set():
      msg = messaging.new_message('eagleState')
      msg.valid = True
      msg.eagleState.vEgo = 12.0
      pm.send('eagleState', msg)
      time.sleep(0.05)

  threading.Thread(target=publish, daemon=True).start()
  try:
    gen = hud.HudStream(params=NS(get=lambda key: None)).events()
    frames = [json.loads(e.split("\n")[1].removeprefix("data: ")) for e in (next(gen) for _ in range(3)) if e.startswith("event: frame")]
    assert frames and frames[-1]["stale"] is False and frames[-1]["vEgo"] == 12.0
  finally:
    stop.set()
