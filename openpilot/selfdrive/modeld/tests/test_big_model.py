# big_model 纯逻辑单测（04 号 C+D）：outputs[0:2066) 解析、L̂ 估计、交叉淡入、bigModelReply schema。
# 跑法：pytest openpilot/selfdrive/modeld/tests/test_big_model.py
import threading
import time
from types import SimpleNamespace

import numpy as np

from openpilot.selfdrive.modeld.big_model import (BIG_OUTPUT_SLICES, parse_big_outputs, LatencyEstimator,
                                                  SourceBlender, BigReplyLatch, nanos_since_boot, select_frame)


def test_nanos_since_boot_matches_timestamp_eof_clock():
  # timestamp_eof = 内核 SOF_BOOT_TS（get_monotonic_boottime64 = CLOCK_BOOTTIME），
  # 与它相减/比较的"现在"必须同一时钟，否则挂起累计量会混进 L_n 和截止
  clock = getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)  # macOS 无 BOOTTIME（同 common/timing.h）
  a = time.clock_gettime_ns(clock)
  now = nanos_since_boot()
  b = time.clock_gettime_ns(clock)
  assert a <= now <= b


def test_big_model_reply_schema():
  # C-1：custom.capnp 预留槽位改名 bigModelReply，字段与 BGM1 REPLY 线布局一一对应
  import openpilot.cereal.messaging as messaging
  msg = messaging.new_message('bigModelReply')
  b = msg.bigModelReply
  b.frameIdx = 7
  b.tEof = 123456789
  b.flags = 3
  b.outputs = [0.5] * 2066
  b.telemetry = [1, 2, 3, 4]
  assert b.frameIdx == 7 and b.tEof == 123456789 and b.flags == 3
  assert len(b.outputs) == 2066 and b.outputs[2065] == 0.5
  assert list(b.telemetry) == [1, 2, 3, 4]


def test_model_data_v2sp_meta_schema():
  # C-2：ModelDataV2SP 扩 bigActionT/desireClass（modeld → bigmodeld 元数据上行）
  import openpilot.cereal.messaging as messaging
  sp = messaging.new_message('modelDataV2SP').modelDataV2SP
  sp.bigActionT = [0.125, 0.45]
  sp.desireClass = 3
  sp.bigLatencyMs = 33.5  # C-3：REPLY 到达 − timestamp_eof（0 = 本帧没等到）；≠ L̂ 的 L_n
  sp.cameraToModelMs = 22.5  # L_n = 收帧时刻 − timestamp_eof（ms），喂 L̂
  np.testing.assert_allclose(list(sp.bigActionT), [0.125, 0.45], rtol=1e-6)  # f32 往返
  assert sp.desireClass == 3
  assert sp.bigLatencyMs == 33.5
  assert sp.cameraToModelMs == 22.5
  sp.bigLateReplyMs = 91.5
  sp.bigLateReplyCount = 2
  assert sp.bigLateReplyMs == 91.5 and sp.bigLateReplyCount == 2


def test_big_output_slices_cover_abi():
  # MODEL_ABI §5：0-2066 无缝覆盖，hidden_state/pad 不在其中
  spans = sorted((s.start, s.stop) for s in BIG_OUTPUT_SLICES.values())
  assert spans[0][0] == 0 and spans[-1][1] == 2066
  for (_, a1), (b0, _) in zip(spans, spans[1:], strict=False):
    assert a1 == b0, f"slice gap/overlap at {a1} vs {b0}"


def test_parse_big_outputs():
  raw = np.zeros(2066, dtype=np.float32)
  raw[2062:2064] = [1.5, -2.0]   # action μ = [横向加速度, 纵向加速度]
  raw[2064:2066] = [0.0, np.log(2.)]  # action logσ
  outs = parse_big_outputs(raw)

  assert set(outs) == set(BIG_OUTPUT_SLICES) | {k + '_stds' for k in ('lane_lines', 'road_edges', 'pose',
                                                                    'wide_from_device_euler', 'road_transform',
                                                                    'plan', 'lead', 'action')}
  assert outs['plan'].shape == (1, 33, 15) and outs['plan_stds'].shape == (1, 33, 15)
  assert outs['lead'].shape == (1, 3, 6, 4) and outs['lead_stds'].shape == (1, 3, 6, 4)
  assert outs['lane_lines'].shape == (1, 4, 33, 2)
  assert outs['pose'].shape == (1, 6)
  assert outs['action'].shape == (1, 2)
  np.testing.assert_allclose(outs['action'][0], [1.5, -2.0])
  np.testing.assert_allclose(outs['action_stds'][0], [1.0, 2.0], rtol=1e-6)
  np.testing.assert_allclose(outs['desire_state'], 1. / 8.)   # softmax(0)
  np.testing.assert_allclose(outs['meta'], 0.5)                # sigmoid(0)


def test_latency_estimator():
  est = LatencyEstimator()
  assert est.value == 22.                 # 初值
  assert est.update(50.) == 35.           # 中位 50 → 限幅上
  est2 = LatencyEstimator()
  assert est2.update(1.) == 15.           # 中位 1 → 限幅下
  est3 = LatencyEstimator()
  for v in (20., 21., 22.):
    got = est3.update(v)
  assert got == 21.                       # 滑动中位
  for v in (30.,) * 100:
    est3.update(v)                        # 窗口 100 帧滑出旧样本
  assert est3.value == 30.


def test_source_blender_passthrough():
  small = {'plan': np.zeros(3, dtype=np.float32)}
  b = SourceBlender(fade_frames=4)
  assert b.step(small, None) is small          # w=0 直通小模型
  big = {'plan': np.ones(3, dtype=np.float32)}
  for _ in range(4):
    b.step(small, big)
  assert b.w == 1.0
  assert b.step(small, big) is big             # w=1 直通大模型（稳定态不滤波）


def test_source_blender_fade():
  small = {'plan': np.zeros(3, dtype=np.float32), 'only_small': np.ones(2, dtype=np.float32)}
  big = {'plan': np.ones(3, dtype=np.float32), 'only_big': np.ones(2, dtype=np.float32)}
  b = SourceBlender(fade_frames=4)

  out = b.step(small, big)                     # 淡入第 1 帧：w=0.25
  assert b.w == 0.25
  np.testing.assert_allclose(out['plan'], 0.25)
  assert out['only_small'] is small['only_small']  # 腿里没有的键不淡

  for _ in range(3):
    b.step(small, big)                         # 到 w=1.0
  out = b.step(small, None)                    # 兜底帧：w→0.75，腿 = hold 的 big
  assert b.w == 0.75
  np.testing.assert_allclose(out['plan'], 0.75)

  for _ in range(3):
    out = b.step(small, None)                  # 淡出到 w=0
  assert b.w == 0.0
  assert out is small


class _FakeSM:
  """接缝假件：永不给消息，只让读线程空转（测试只测 _on_reply/wait_for 接缝）。"""
  updated = {'bigModelReply': False}
  def update(self, timeout=0.0):
    time.sleep(min(timeout, 0.05))
  def __getitem__(self, k):
    raise KeyError(k)


def test_big_reply_latch():
  # C-3：读线程收帧即盖真实到达时刻（不被 model.run() 遮挡）、按 tEof 匹配、
  # 已到且按时零等待取、超时/迟到兜底、迟到 Condition 唤醒、链路存活门。
  # REPLY 往返只进 bigLatencyMs 遥测，不进 L̂（L̂ = modeld 收帧延迟，ADR-0001）
  latch = BigReplyLatch(_FakeSM())
  raw = np.arange(2066, dtype=np.float32)
  t_eof = nanos_since_boot() - 30_000_000

  assert not latch.link_alive()                       # 从未见过 REPLY → 门关（只捡不等）
  assert not hasattr(latch, 'latency')                # latch 不持有 L̂

  latch._on_reply(t_eof, raw, t_eof + 25_000_000)     # 到达 = eof+25 ms
  assert latch.link_alive()

  got, eof_to_reply_ms = latch.wait_for(t_eof, nanos_since_boot() + 10_000_000_000)
  assert got is raw and eof_to_reply_ms == 25.                  # 已到且按时：零等待取

  got, eof_to_reply_ms = latch.wait_for(t_eof + 1, nanos_since_boot())
  assert got is None and eof_to_reply_ms == 0.                  # 不匹配 + 过期 → 兜底

  # 迟到 REPLY 已在手也不用（按截止时刻择优）
  late_eof = t_eof + 1_000_000
  latch._on_reply(late_eof, raw, late_eof + 60_000_000)          # 到达 = eof+60 ms
  got, eof_to_reply_ms = latch.wait_for(late_eof, late_eof + 40_000_000)   # deadline = eof+40 ms
  assert got is None and eof_to_reply_ms == 0.

  threading.Timer(0.05, latch._on_reply, args=(t_eof + 2, raw, nanos_since_boot())).start()
  got, eof_to_reply_ms = latch.wait_for(t_eof + 2, nanos_since_boot() + 5_000_000_000)
  assert got is raw                                   # 等 Condition，一到即醒

  latch.alive_s = 0.0
  assert not latch.link_alive()                       # 没回音 → 门关


def test_big_reply_latch_counts_late_replies():
  # 截止后才到的 REPLY：take 取走（自上次 take 起的迟到个数, 最近一个的往返 ms），按时的不算
  latch = BigReplyLatch(_FakeSM())
  raw = np.ones(2066, dtype=np.float32)
  t_eof = nanos_since_boot() - 200_000_000

  latch._on_reply(t_eof, raw, t_eof + 25_000_000)
  latch.wait_for(t_eof, t_eof + 70_000_000)
  assert latch.take_late_replies() == (0, 0.)         # 按时到达不算迟到

  gave_up = t_eof + 1_000_000
  assert latch.wait_for(gave_up, nanos_since_boot())[0] is None   # 截止时还没到 → 放弃
  latch._on_reply(gave_up, raw, gave_up + 90_000_000)              # 放弃后才到
  assert latch.take_late_replies() == (1, 90.)
  assert latch.take_late_replies() == (0, 0.)         # 只报一次

  in_hand = t_eof + 2_000_000
  latch._on_reply(in_hand, raw, in_hand + 75_000_000)
  assert latch.wait_for(in_hand, in_hand + 70_000_000)[0] is None  # 已在手但过了截止
  gave_up2 = t_eof + 3_000_000
  assert latch.wait_for(gave_up2, nanos_since_boot())[0] is None
  latch._on_reply(gave_up2, raw, gave_up2 + 95_000_000)
  assert latch.take_late_replies() == (2, 95.)        # 两次 take 之间迟到两个：都计数，往返报最近一个

  early = t_eof + 4_000_000
  latch._on_reply(early, raw, early + 20_000_000)     # 还没轮到决策的帧先到 → 不是迟到
  assert latch.take_late_replies() == (0, 0.)


class _FakeReplySelector:
  def __init__(self, *, alive, result):
    self.alive = alive
    self.result = result
    self.waited = []

  def link_alive(self):
    return self.alive

  def wait_for(self, t_eof, deadline_ns):
    self.waited.append((t_eof, deadline_ns))
    return self.result


def test_select_frame_obeys_warmup_enable_and_link_gates():
  latch = _FakeReplySelector(alive=True, result=(np.ones(2), 25.))
  small = {"plan": np.array([2.], dtype=np.float32)}
  blender = SourceBlender(fade_frames=2)
  action_calls = []

  def action_for(output, lat_t, long_t):
    action_calls.append((output, lat_t, long_t))
    value = float(output["plan"][0])
    return SimpleNamespace(desiredCurvature=value, desiredAcceleration=-value, shouldStop=value < 1.5)

  def select(enabled, run_count):
    return select_frame(
      small, latch=latch, enabled=enabled, run_count=run_count, timestamp_eof=1_000_000_000,
      latency_ms=22., grace_ms=48., warmup_frames=4, current_time_ns=lambda: 9_000_000_000, blender=blender,
      action_for=action_for, small_action_t=(0.1, 0.2), big_action_t=(0.3, 0.4),
      parse_outputs=lambda raw: {"plan": np.array([raw[0]], dtype=np.float32)})

  first = select(enabled=True, run_count=4)
  second = select(enabled=False, run_count=5)
  assert latch.waited == []
  assert first.big_output is None and first.eof_to_reply_ms == 0.
  assert second.big_output is None and second.model_output is small

  selected = select(enabled=True, run_count=5)
  assert latch.waited == [(1_000_000_000, 1_070_000_000)]
  assert selected.big_output["plan"][0] == 1.
  np.testing.assert_allclose(selected.model_output["plan"], [1.5])
  assert selected.eof_to_reply_ms == 25.
  assert selected.desired_curvature == 1.5 and selected.desired_acceleration == -1.5
  assert selected.should_stop is True  # weight=.5 selects the big-model stop bit
  assert action_calls[-2][1:] == (0.1, 0.2) and action_calls[-1][1:] == (0.3, 0.4)

  latch.alive = False
  select(enabled=True, run_count=5)
  assert latch.waited[-1] == (1_000_000_000, 9_000_000_000)


def test_select_frame_rejects_zero_reply():
  latch = _FakeReplySelector(alive=True, result=(np.zeros(2066, dtype=np.float32), 12.))
  small = {"plan": np.array([1.], dtype=np.float32)}
  frame = select_frame(small, latch=latch, enabled=True, run_count=5, timestamp_eof=1, latency_ms=22.,
                       grace_ms=48., warmup_frames=4, current_time_ns=lambda: 10, blender=SourceBlender(),
                       action_for=lambda *_: SimpleNamespace(desiredCurvature=0., desiredAcceleration=0.,
                                                            shouldStop=False),
                       small_action_t=(0., 0.), big_action_t=(0., 0.),
                       parse_outputs=lambda raw: {"plan": raw})
  assert frame.big_output is None and frame.eof_to_reply_ms == 12.
  assert frame.model_output is small


def test_modeld_c3_wiring_source():
  """modeld 主循环需 QCOM GPU 无法宿主构造，照 test_is_run_model 手法锁票面不变量
  （头 4 帧超时帧、全零兜底、modelV2.big、L_n 遥测）。接线一起改的话本测试会提醒更新。"""
  import inspect
  from openpilot.selfdrive.modeld import modeld
  src = inspect.getsource(modeld.main)
  assert "BIG_WARMUP_FRAMES" in src, "modeld 重启后头 4 帧按超时帧（04 号票）"
  assert "select_frame(" in src, "逐帧 REPLY 策略必须通过宿主可测的决策 seam"
  assert "modelV2.big = big_out is not None" in src
  assert "bigLatencyMs" in src, "REPLY 往返每帧进遥测（04 号票）"
  assert "bigLateReplyCount, mdv2sp_send.modelDataV2SP.bigLateReplyMs = latch.take_late_replies()" in src, \
    "迟到 REPLY 个数与往返也进遥测"
  # ADR-0001：L̂ 只喂 modeld 收帧时刻 − timestamp_eof（cameraToModelMs），截止用它；
  # REPLY 往返不得进 L̂，每帧 L_n 进遥测
  assert "camera_to_model_ms = (nanos_since_boot() - meta_main.timestamp_eof) / 1e6" in src
  # 与 timestamp_eof 比较的"现在"全走 BOOTTIME（modeld 主循环 + latch）
  assert "monotonic_ns" not in src
  assert "monotonic_ns" not in inspect.getsource(BigReplyLatch)
  assert "latency.update(camera_to_model_ms)" in src
  assert "cameraToModelMs = camera_to_model_ms" in src
  assert "latch.latency" not in src
