"""VisionWorker 异步推理执行器测试:主循环不阻塞、结果落库、过期代际丢弃。

背景(2026-09-25 路测):视觉帧(取帧+转换+YOLO)在主循环里占 ~390ms,Ratekeeper
追帧补发,lateralManeuverPlan 间隔呈 4ms/395ms 锯齿。把推理挪到工作线程,
plan 每拍只做融合+发布,视觉结果异步落进 hold。
"""
import threading
import time

from openpilot.selfdrive.eagled.perception import PerceptionCore, VisionWorker
from openpilot.selfdrive.eagled.tests.test_daemon_fusion import ROI, _FakeCamera, _core_sm, _box_at

PERSON_BOX = _box_at(20.0, -1.8, cls="person")


class _GatedDetector:
  """infer 挂起直到 gate 放行,用于确定性地控制推理完成时刻。"""

  def __init__(self, gate: threading.Event, started: threading.Event, finished: threading.Event):
    self.gate, self.started, self.finished = gate, started, finished
    self.calls = 0

  def infer(self, roi, now=None):
    self.calls += 1
    self.started.set()
    self.gate.wait(timeout=5.0)
    self.finished.set()
    return [PERSON_BOX]


def _gated_core():
  gate = threading.Event()
  started = threading.Event()
  finished = threading.Event()
  det = _GatedDetector(gate, started, finished)
  worker = VisionWorker()
  core = PerceptionCore(camera=_FakeCamera(frames=[ROI] * 4), detector=det, vision_worker=worker)
  return core, det, worker, gate, started, finished


def _wait_held(core, worker, v_ego=20.0, now=0.2, timeout=2.0):
  """轮询 process() 直到 hold 里出现检测(工作线程结果被吸收)。"""
  frame = None
  for _ in range(int(timeout / 0.01)):
    frame = core.process(_core_sm(), now, v_ego, vision_enabled=True, vision_due=False)
    if frame.detections:
      return frame
    time.sleep(0.01)
  return frame


def test_worker_runs_job_and_poll_returns_result_once():
  w = VisionWorker()
  try:
    w.submit(lambda: 42)
    res = None
    for _ in range(200):
      res = w.poll()
      if res is not None:
        break
      time.sleep(0.01)
    assert res == 42
    assert w.poll() is None                      # 结果只取一次
  finally:
    w.stop()


def test_main_loop_does_not_block_on_inference():
  """due 拍 submit 后立即返回,推理挂起期间主循环不受影响。"""
  core, det, worker, gate, started, finished = _gated_core()
  try:
    frame = core.process(_core_sm(), 0.0, 20.0, vision_enabled=True, vision_due=True)
    assert frame.detections == []                # 本拍沿用空 hold
    assert started.wait(2.0)                     # 工作线程确实在跑
    assert not finished.is_set()                 # 但推理未完成 —— process() 没等它
    gate.set()                                   # 放行
    frame = _wait_held(core, worker)
    assert len(frame.detections) == 1            # 结果异步落进 hold
  finally:
    worker.stop()


def test_late_worker_result_discarded_after_disable():
  """避让关闭(代际+1)后到达的推理结果不得复活旧目标。"""
  core, det, worker, gate, started, finished = _gated_core()
  try:
    core.process(_core_sm(), 0.0, 20.0, vision_enabled=True, vision_due=True)
    assert started.wait(2.0)
    core.process(_core_sm(), 0.1, 20.0, vision_enabled=False, vision_due=False)  # 清 hold + 代际
    gate.set()
    finished.wait(2.0)
    time.sleep(0.05)                             # 让结果落入 worker 邮箱
    frame = core.process(_core_sm(), 0.2, 20.0, vision_enabled=True, vision_due=False)
    assert frame.detections == []                # 迟到结果被丢弃
  finally:
    worker.stop()


def test_sync_path_unchanged_without_worker():
  """无 worker(单测/影子工具默认):detect 同步执行,行为与历史一致。"""
  class _Quick:
    def infer(self, roi, now=None):
      return [PERSON_BOX]
  core = PerceptionCore(camera=_FakeCamera(frames=[ROI]), detector=_Quick())
  frame = core.process(_core_sm(), 0.0, 20.0, vision_enabled=True, vision_due=True)
  assert len(frame.detections) == 1              # 同拍即得
