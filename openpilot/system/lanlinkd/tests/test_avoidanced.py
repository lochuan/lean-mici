"""avoidanced.AvoidanceCache 的真实链路测试（发布器 → SubMaster → 快照）。"""
import threading
import time

import pytest

from openpilot.cereal import messaging
from openpilot.system.lanlinkd import avoidanced

from .fake_params import FakeParams


def _publish_debug(pm: messaging.PubMaster) -> None:
  msg = messaging.new_message('eagleDebug')
  dbg = msg.eagleDebug
  dbg.valid = True
  dbg.active = True
  dbg.direction = 1
  dbg.yDes = 0.3
  dbg.bias = 0.01
  dbg.maxOffset = 0.35
  dbg.bsmRight = True
  dbg.vEgo = 25.0
  dbg.nRadar = 1
  dbg.nVision = 1
  dbg.nAssociated = 1
  dbg.edgeClearance = 999.0
  dbg.canError = False
  dbg.radarUnavailable = True
  dbg.laneLeftValid = True
  dbg.laneRightValid = False
  dbg.budgetLeft = 0.9
  dbg.budgetRight = 999.0
  dbg.changeClearLeft = True
  dbg.changeClearRight = False
  tgts = dbg.init('targets', 2)
  tgts[0].dRel, tgts[0].yRel, tgts[0].vRel = 20.0, -1.0, 1.5
  tgts[0].matched, tgts[0].inGate, tgts[0].pairId = True, True, 1
  tgts[0].lane = 1
  tgts[1].dRel, tgts[1].yRel = 25.0, -1.2
  tgts[1].cls, tgts[1].conf, tgts[1].vision = "person", 0.9, True
  tgts[1].matched, tgts[1].inGate, tgts[1].pairId = True, True, 1
  tgts[1].weight = 1.0
  tgts[1].lane = 1
  pm.send('eagleDebug', msg)


@pytest.fixture
def publisher() -> messaging.PubMaster:
  return messaging.PubMaster(['eagleDebug'])


def _start_cache() -> tuple[avoidanced.AvoidanceCache, threading.Event, threading.Thread]:
  cache = avoidanced.AvoidanceCache(FakeParams())
  exit_event = threading.Event()
  t = threading.Thread(target=cache.run, args=(exit_event,), daemon=True)
  t.start()
  return cache, exit_event, t


def test_cache_captures_published_debug(publisher):
  from openpilot.common.stream_gate import MAX_AGE_S
  assert MAX_AGE_S["eagleDebug"] == 1.0  # 5Hz 数据：1s 无新帧即视为停更
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):  # 5Hz 数据，多发几帧等 SubMaster 收到
      _publish_debug(publisher)
      time.sleep(0.1)
      if cache.snapshot().get("stale") is False:
        break
    snap = cache.snapshot()
  finally:
    exit_event.set()
    t.join(timeout=2)

  assert snap["stale"] is False
  assert snap["logMonoTime"] > 0
  assert snap["valid"] is True
  assert snap["active"] is True
  assert snap["direction"] == 1
  assert snap["yDes"] == pytest.approx(0.3)
  assert snap["maxOffset"] == pytest.approx(0.35)
  assert snap["bsmRight"] is True
  assert snap["vEgo"] == pytest.approx(25.0)
  assert snap["edgeClearance"] == pytest.approx(999.0)
  assert snap["canError"] is False
  assert snap["radarUnavailable"] is True
  # C2/C9 新字段透传
  assert snap["laneLeftValid"] is True and snap["laneRightValid"] is False
  assert snap["budgetLeft"] == pytest.approx(0.9)
  assert snap["budgetRight"] == pytest.approx(999.0)
  assert snap["changeClearLeft"] is True and snap["changeClearRight"] is False
  assert (snap["nRadar"], snap["nVision"], snap["nAssociated"]) == (1, 1, 1)
  assert len(snap["targets"]) == 2
  radar_t, vision_t = snap["targets"]
  assert vision_t["cls"] == "person" and vision_t["vision"] is True
  assert radar_t["vision"] is False and radar_t["vRel"] == pytest.approx(1.5)
  assert vision_t["lane"] == radar_t["lane"] == 1   # C2 车道归属随目标透传
  # 配对双方共享同一 pairId
  assert vision_t["pairId"] == radar_t["pairId"] == 1


def test_cache_goes_stale_after_silence(publisher, monkeypatch):
  import openpilot.common.stream_gate as stream_gate
  monkeypatch.setitem(stream_gate.MAX_AGE_S, "eagleDebug", 0.2)
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(10):
      _publish_debug(publisher)
      time.sleep(0.05)
      if cache.snapshot().get("stale") is False:
        break
    assert cache.snapshot()["stale"] is False
    time.sleep(0.4)  # 静默超过调低后的登记阈值
    assert cache.snapshot()["stale"] is True
  finally:
    exit_event.set()
    t.join(timeout=2)


# --- 标定状态透传 ---------------------------------------------------------
# 未标定时 avoidanced 会整体关掉视觉路径,前端只会看到 nVision 恒为 0 而没有
# 任何解释。EagleDebug 的 capnp 结构里没有降级原因字段,而 openpilot/cereal
# 在 release_lib 的 NATIVE_INPUT_PATHS 里 —— 加字段要设备全量重建。所以标定
# 状态由 lanlinkd 自己订阅 extrinsicsCalibration 透传。

def _publish_calibration(pm: messaging.PubMaster, status: str, perc: int, valid: bool = True) -> None:
  msg = messaging.new_message('extrinsicsCalibration')
  msg.valid = valid   # new_message 默认 valid=False;快照要求 valid 才认标定
  cal = msg.extrinsicsCalibration
  cal.calStatus = status
  cal.calPerc = perc
  if status == "calibrated":
    cal.rpyCalib = [0.0, 0.01, 0.0]
  pm.send('extrinsicsCalibration', msg)


@pytest.fixture
def cal_publisher() -> messaging.PubMaster:
  return messaging.PubMaster(['eagleDebug', 'extrinsicsCalibration'])


def test_cache_reports_uncalibrated_so_the_ui_can_explain_no_vision(cal_publisher):
  """未标定时快照必须说明原因,否则 nVision=0 在前端无从解释。"""
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):
      _publish_debug(cal_publisher)
      _publish_calibration(cal_publisher, "uncalibrated", 42)
      time.sleep(0.1)
      if cache.snapshot().get("calStatus") == "uncalibrated":
        break
    snap = cache.snapshot()
    assert snap["calStatus"] == "uncalibrated"
    assert snap["calPerc"] == 42
    assert snap["calValid"] is False
    assert snap["visionGated"] is True   # 前端据此解释 nVision=0
  finally:
    exit_event.set()
    t.join(timeout=2)


def test_cache_reports_calibrated(cal_publisher):
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):
      _publish_debug(cal_publisher)
      _publish_calibration(cal_publisher, "calibrated", 100)
      time.sleep(0.1)
      if cache.snapshot().get("calStatus") == "calibrated":
        break
    snap = cache.snapshot()
    assert snap["calValid"] is True
    assert snap["visionGated"] is False
  finally:
    exit_event.set()
    t.join(timeout=2)


def test_cache_does_not_trust_calibrated_status_on_an_invalid_message(cal_publisher):
  """calStatus 说已标定但消息无效时不能当成已标定。

  与 projection.geometry_from_calibration 的判定保持一致 —— 那边同样要求
  valid 且 rpyCalib 长度为 3,一个空的 rpyCalib 配 "calibrated" 不能当成
  零角度使用。两处判定若分叉,前端就会声称视觉在跑而 daemon 其实关掉了。
  """
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):
      _publish_debug(cal_publisher)
      _publish_calibration(cal_publisher, "calibrated", 100, valid=False)
      time.sleep(0.1)
      if cache.snapshot().get("calStatus") == "calibrated":
        break
    snap = cache.snapshot()
    assert snap["calStatus"] == "calibrated"
    assert snap["calValid"] is False      # 消息无效 -> 不认
    assert snap["visionGated"] is True
  finally:
    exit_event.set()
    t.join(timeout=2)


# --- 车道几何透传（lanes.py，方案 A：lanlinkd 自己订阅 modelV2） -------------------

def test_snapshot_includes_lanes_field(cal_publisher):
  """快照必须带 lanes 键：有 modelV2 帧时是车道 dict，没有时是 None——
  前端据此决定画不画车道层，字段缺失语义与 null 一致。"""
  from openpilot.cereal import messaging as _m
  model_pub = _m.PubMaster(['modelV2'])
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):
      _publish_debug(cal_publisher)
      msg = _m.new_message('modelV2')
      msg.modelV2.init('laneLines', 4)
      msg.modelV2.laneLines[1].x, msg.modelV2.laneLines[1].y = [0.0, 50.0], [-1.75, -1.75]
      msg.modelV2.laneLineProbs = [0.1, 0.9, 0.1, 0.1]
      msg.modelV2.laneLineStds = [0.5, 0.1, 0.5, 0.5]
      model_pub.send('modelV2', msg)
      time.sleep(0.05)
      snap = cache.snapshot()
      if isinstance(snap.get("lanes"), dict):
        break
    assert isinstance(snap["lanes"], dict)
    # 双集契约：corrected（车体系）与 raw（换算前）同网格，raw 与换算前行为逐点一致
    assert snap["lanes"]["corrected"]["laneLines"][1]["y"][0] == pytest.approx(1.75)
    assert snap["lanes"]["raw"]["laneLines"][1]["y"][0] == pytest.approx(1.75)
  finally:
    exit_event.set()
    t.join(timeout=2)


def test_snapshot_lanes_is_none_without_model_v2(publisher):
  cache, exit_event, t = _start_cache()
  try:
    for _ in range(20):
      _publish_debug(publisher)
      time.sleep(0.1)
      if cache.snapshot().get("stale") is False:
        break
    snap = cache.snapshot()
    assert snap["stale"] is False
    assert snap["lanes"] is None     # 无 modelV2 帧 -> 无车道层
  finally:
    exit_event.set()
    t.join(timeout=2)
