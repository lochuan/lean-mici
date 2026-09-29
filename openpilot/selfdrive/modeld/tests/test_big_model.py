# big_model 纯逻辑单测（04 号 C+D）：outputs[0:2066) 解析、L̂ 估计、交叉淡入、bigModelReply schema。
# 跑法：pytest openpilot/selfdrive/modeld/tests/test_big_model.py
import threading
import time

import numpy as np

from openpilot.selfdrive.modeld.big_model import (BIG_OUTPUT_SLICES, parse_big_outputs, LatencyEstimator,
                                                  SourceBlender, BigReplyLatch)


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
  sp.bigLatencyMs = 33.5  # C-3：L_n = 收帧时刻 − timestamp_eof（0 = 本帧没等到）
  np.testing.assert_allclose(list(sp.bigActionT), [0.125, 0.45], rtol=1e-6)  # f32 往返
  assert sp.desireClass == 3
  assert sp.bigLatencyMs == 33.5


def test_big_output_slices_cover_abi():
  # MODEL_ABI §5：0-2066 无缝覆盖，hidden_state/pad 不在其中
  spans = sorted((s.start, s.stop) for s in BIG_OUTPUT_SLICES.values())
  assert spans[0][0] == 0 and spans[-1][1] == 2066
  for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
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
  # 已到零等待取、过期兜底、迟到 Condition 唤醒、L_n 进 L̂、链路存活门
  latch = BigReplyLatch(_FakeSM())
  raw = np.arange(2066, dtype=np.float32)
  t_eof = 1_000_000_000

  assert not latch.link_alive()                       # 从未见过 REPLY → 门关（只捡不等）

  latch._on_reply(t_eof, raw, t_eof + 25_000_000)     # 到达 = eof+25 ms
  assert latch.link_alive()
  assert latch.latency.value == 25.                   # L_n=25 ms 进 L̂（中位 25）

  got, arrival_ns = latch.wait_for(t_eof, time.monotonic_ns() + 10_000_000_000)
  assert got is raw and arrival_ns == t_eof + 25_000_000   # 已到即取，不等 deadline

  got, arrival_ns = latch.wait_for(t_eof + 1, time.monotonic_ns())
  assert got is None and arrival_ns == 0              # 不匹配 + 过期 → 兜底

  threading.Timer(0.05, latch._on_reply, args=(t_eof + 2, raw, t_eof + 2)).start()
  got, arrival_ns = latch.wait_for(t_eof + 2, time.monotonic_ns() + 5_000_000_000)
  assert got is raw                                   # 等 Condition，一到即醒

  latch.alive_s = 0.0
  assert not latch.link_alive()                       # 没回音 → 门关


def test_modeld_c3_wiring_source():
  """modeld 主循环需 QCOM GPU 无法宿主构造，照 test_is_run_model 手法锁票面不变量
  （头 4 帧超时帧、全零兜底、modelV2.big、L_n 遥测）。接线一起改的话本测试会提醒更新。"""
  import inspect
  from openpilot.selfdrive.modeld import modeld
  src = inspect.getsource(modeld.main)
  assert "BIG_WARMUP_FRAMES" in src, "modeld 重启后头 4 帧按超时帧（04 号票）"
  assert "np.any" in src, "REPLY outputs 全零 = 解码跳过帧，须落回小模型（14 号口径）"
  assert "modelV2.big = big_out is not None" in src
  assert "bigLatencyMs" in src, "L_n 每帧进遥测（04 号票）"
