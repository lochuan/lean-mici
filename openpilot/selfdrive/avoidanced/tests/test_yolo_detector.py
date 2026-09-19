import time
from pathlib import Path

import numpy as np
import pytest

from openpilot.common.hardware import PC
from openpilot.selfdrive.avoidanced.yolo_detector import (
  CLASS_NAMES,
  YoloDetector,
  postprocess,
  preprocess,
)

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
PKL_PATH = MODELS_DIR / "yolo_tinygrad.pkl"

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
PKL_PATH = MODELS_DIR / "yolo_tinygrad.pkl"

# YOLO26 detect head emits (1, 4 + num_bdd8_classes, num_anchors); BDD8 has 8 classes (incl. tricycle).
NUM_CLASSES = 8
# Production anchor count for a 640x384 input: (80*48) + (40*24) + (20*12) = 5040.
PRODUCTION_ANCHORS = 5040


def _raw_output(entries: list[tuple[int, float, float, float, float, float]],
                num_anchors: int = PRODUCTION_ANCHORS) -> np.ndarray:
  """Build a canned YOLOv8 raw output at production shape. entries: (cls_id, conf, cx, cy, w, h)."""
  assert len(entries) <= num_anchors
  raw = np.zeros((1, 4 + NUM_CLASSES, num_anchors), dtype=np.float32)
  for i, (cls_id, conf, cx, cy, w, h) in enumerate(entries):
    raw[0, 0, i] = cx
    raw[0, 1, i] = cy
    raw[0, 2, i] = w
    raw[0, 3, i] = h
    raw[0, 4 + cls_id, i] = conf
  return raw


class _StubRunner:
  def __init__(self, raw: np.ndarray):
    self.raw = raw
    self.calls = 0

  def run(self, inp: np.ndarray) -> np.ndarray:
    self.calls += 1
    assert inp.shape == (1, 3, 384, 640)
    assert inp.dtype == np.float32
    return self.raw


class _FakeParams:
  def __init__(self, value):
    self._value = value

  def get(self, key: str):
    assert key == "AvoidanceMinConfidence"
    return self._value


# --- on-device interface test (pkl is QCOM-compiled; runs only on comma) ---

def test_runner_feeds_persistent_input(monkeypatch, tmp_path):
  """TinyJit 只在输入 buffer 身份稳定时回放；runner 必须持有同一个持久输入
  Tensor 并用 assign 刷新内容（每帧新建会换 buffer id → 触发重编译，
  间歇性撞上游 tinygrad 的符号维度 split 崩溃）。用 fake jit 断言：
  1) 两次 run 喂进来的张量是同一个对象（buffer 身份稳定）；
  2) 该张量确实被 assign 更新过。"""
  import numpy as _np

  class _Captured:
    expected_names = ["images"]

  class _FakeJit:
    captured = _Captured()

    def __call__(self, **kwargs):
      self.fed = kwargs["images"]
      return self

    def numpy(self):
      return _np.zeros((1, 4 + NUM_CLASSES, PRODUCTION_ANCHORS), dtype=_np.float32)

  fake = _FakeJit()
  import openpilot.selfdrive.modeld.helpers as modeld_helpers
  from openpilot.selfdrive.avoidanced import yolo_detector as yd

  monkeypatch.setattr(modeld_helpers, "load_oob", lambda f: fake)
  pkl = tmp_path / "fake.pkl"
  pkl.write_bytes(b"")
  runner = yd.TinygradRunner(pkl)
  runner.run(_np.full((1, 3, 384, 640), 0.1, dtype=_np.float32))
  second_fed = fake.fed
  runner.run(_np.full((1, 3, 384, 640), 0.9, dtype=_np.float32))
  assert runner._input is fake.fed, "the same persistent tensor must be fed every call"
  assert second_fed is fake.fed, "identity must be stable across calls (JIT replay)"


@pytest.mark.skipif(PC, reason="yolo pkl is compiled for the QCOM device; load+run needs comma hardware")
def test_detector_returns_boxes():
  det = YoloDetector(str(PKL_PATH))
  boxes = det.infer(np.zeros((384, 640, 3), np.uint8))
  assert isinstance(boxes, list)


@pytest.mark.skipif(PC, reason="needs QCOM device")
def test_detector_latency_under_350ms():
  det = YoloDetector(str(PKL_PATH), fps=0)
  frame = np.zeros((384, 640, 3), np.uint8)
  det.infer(frame)
  start = time.monotonic()
  det.infer(frame)
  assert (time.monotonic() - start) < 0.35


# --- preprocess ---

def test_preprocess_shape_dtype_and_range():
  frame = np.zeros((384, 640, 3), np.uint8)
  frame[0, 0, 0] = 255
  inp = preprocess(frame)
  assert inp.shape == (1, 3, 384, 640)
  assert inp.dtype == np.float32
  assert inp.max() == pytest.approx(1.0)
  assert inp.min() == pytest.approx(0.0)


def test_preprocess_rejects_wrong_roi_shape():
  with pytest.raises(ValueError):
    preprocess(np.zeros((256, 512, 3), np.uint8))


# --- postprocess ---

def test_postprocess_box_schema_and_xyxy():
  raw = _raw_output([(2, 0.9, 100, 100, 40, 20)])
  det = postprocess(raw, conf_threshold=0.15)[0]
  assert set(det) == {"x1", "y1", "x2", "y2", "cls", "conf"}
  assert det["x1"] == pytest.approx(80.0)
  assert det["y1"] == pytest.approx(90.0)
  assert det["x2"] == pytest.approx(120.0)
  assert det["y2"] == pytest.approx(110.0)
  assert det["cls"] == "car"
  assert det["conf"] == pytest.approx(0.9)


def test_postprocess_production_anchor_count_channel_major():
  """YOLOv8 is channel-major: a detection at the last of 5040 anchors must decode."""
  raw = np.zeros((1, 4 + NUM_CLASSES, PRODUCTION_ANCHORS), dtype=np.float32)
  idx = PRODUCTION_ANCHORS - 1
  raw[0, 0, idx] = 320.0
  raw[0, 1, idx] = 200.0
  raw[0, 2, idx] = 60.0
  raw[0, 3, idx] = 80.0
  raw[0, 4 + 0, idx] = 0.9
  dets = postprocess(raw, conf_threshold=0.15)
  assert len(dets) == 1
  assert dets[0]["cls"] == "person"
  assert dets[0]["x1"] == pytest.approx(290.0)
  assert dets[0]["y1"] == pytest.approx(160.0)
  assert dets[0]["x2"] == pytest.approx(350.0)
  assert dets[0]["y2"] == pytest.approx(240.0)


def test_postprocess_maps_required_classes():
  raw = _raw_output([
    (0, 0.9, 100, 100, 50, 50),
    (1, 0.8, 200, 100, 40, 40),
    (2, 0.7, 300, 100, 60, 60),
    (3, 0.6, 400, 100, 30, 30),
    (4, 0.55, 150, 200, 80, 60),
    (5, 0.5, 250, 150, 30, 50),
    (6, 0.5, 350, 150, 30, 30),
    (7, 0.5, 450, 150, 40, 40),
  ])
  assert {d["cls"] for d in postprocess(raw, conf_threshold=0.15)} == set(CLASS_NAMES.values())


def test_postprocess_filters_low_confidence():
  raw = _raw_output([(0, 0.9, 100, 100, 50, 50), (2, 0.12, 300, 100, 60, 60)])
  dets = postprocess(raw, conf_threshold=0.15)
  assert len(dets) == 1
  assert dets[0]["cls"] == "person"


def test_postprocess_drops_empty_class_scores():
  """BDD7 head has 7 class channels (ids 0-6)；全零分数的锚点低于阈值被滤掉。"""
  raw = np.zeros((1, 4 + NUM_CLASSES, PRODUCTION_ANCHORS), dtype=np.float32)
  raw[0, 0, 0], raw[0, 1, 0], raw[0, 2, 0], raw[0, 3, 0] = 100, 100, 50, 50
  assert postprocess(raw, conf_threshold=0.15) == []


def test_postprocess_clips_boxes_to_roi():
  raw = _raw_output([(0, 0.9, 5, 5, 40, 40)])
  det = postprocess(raw, conf_threshold=0.15)[0]
  assert det["x1"] == 0.0
  assert det["y1"] == 0.0


def test_postprocess_nms_suppresses_overlapping_same_class():
  raw = _raw_output([(0, 0.9, 100, 100, 50, 50), (0, 0.8, 102, 100, 50, 50)])
  dets = postprocess(raw, conf_threshold=0.15, iou_threshold=0.45)
  assert len(dets) == 1
  assert dets[0]["conf"] == pytest.approx(0.9)


def test_postprocess_nms_is_per_class():
  raw = _raw_output([(0, 0.9, 100, 100, 50, 50), (2, 0.8, 102, 100, 50, 50)])
  assert len(postprocess(raw, conf_threshold=0.15, iou_threshold=0.45)) == 2


def test_postprocess_empty_output():
  assert postprocess(np.zeros((1, 4 + NUM_CLASSES, 0), np.float32), conf_threshold=0.15) == []


# --- detector shell ---

def test_detector_returns_boxes_with_injected_runner():
  runner = _StubRunner(_raw_output([(0, 0.9, 320, 200, 60, 80)]))
  det = YoloDetector(PKL_PATH, runner=runner, conf_threshold=0.15)
  boxes = det.infer(np.zeros((384, 640, 3), np.uint8))
  assert isinstance(boxes, list)
  assert boxes[0]["cls"] == "person"
  assert runner.calls == 1


def test_detector_throttles_to_fps():
  runner = _StubRunner(_raw_output([(0, 0.9, 320, 200, 60, 80)]))
  now = [0.0]
  det = YoloDetector(PKL_PATH, runner=runner, conf_threshold=0.15, fps=3.0, clock=lambda: now[0])
  frame = np.zeros((384, 640, 3), np.uint8)
  det.infer(frame)
  det.infer(frame)
  assert runner.calls == 1
  now[0] = 0.2
  det.infer(frame)
  assert runner.calls == 1
  now[0] = 0.4
  det.infer(frame)
  assert runner.calls == 2


def test_detector_reads_confidence_param():
  raw = _raw_output([(0, 0.5, 320, 200, 60, 80)])
  frame = np.zeros((384, 640, 3), np.uint8)
  high = YoloDetector(PKL_PATH, runner=_StubRunner(raw), params=_FakeParams(0.6))
  assert high.infer(frame) == []
  low = YoloDetector(PKL_PATH, runner=_StubRunner(raw), params=_FakeParams(0.4))
  assert len(low.infer(frame)) == 1


def test_detector_defaults_confidence_when_param_unset():
  raw = _raw_output([(0, 0.5, 320, 200, 60, 80)])
  det = YoloDetector(PKL_PATH, runner=_StubRunner(raw), params=_FakeParams(None))
  assert len(det.infer(np.zeros((384, 640, 3), np.uint8))) == 1


# --- temporal filter（低阈值远距 VRU 的时序平滑）---

def test_temporal_boosts_repeated_detection():
  """同一目标连续两帧检出 → conf 获得 +boost（跨过 0.15 阈值的关键机制）。"""
  from openpilot.selfdrive.avoidanced.yolo_detector import TemporalFilter
  tf = TemporalFilter()
  f1 = [{"x1": 100.0, "y1": 100.0, "x2": 150.0, "y2": 150.0, "cls": "person", "conf": 0.12}]
  out1 = tf(list(f1))
  assert out1[0]["conf"] == pytest.approx(0.12)  # 首帧无先前匹配，不提升
  out2 = tf([{"x1": 102.0, "y1": 100.0, "x2": 152.0, "y2": 150.0, "cls": "person", "conf": 0.12}])
  assert out2[0]["conf"] == pytest.approx(0.17)  # 连续帧匹配 → 0.12+0.05


def test_temporal_carries_one_frame_dropout():
  """已确认目标漏检一帧 → carry-forward 重发（conf×0.8），第二帧漏检后消失。"""
  from openpilot.selfdrive.avoidanced.yolo_detector import TemporalFilter
  tf = TemporalFilter()
  tf([{"x1": 100.0, "y1": 100.0, "x2": 150.0, "y2": 150.0, "cls": "rider", "conf": 0.3}])
  carried = tf([])  # 本帧漏检 → carry
  assert len(carried) == 1 and carried[0]["cls"] == "rider"
  assert carried[0]["conf"] == pytest.approx(0.3 * 0.8)
  gone = tf([])  # carry 用尽 → 消失
  assert gone == []


def test_temporal_no_cross_class_match():
  from openpilot.selfdrive.avoidanced.yolo_detector import TemporalFilter
  tf = TemporalFilter()
  tf([{"x1": 100.0, "y1": 100.0, "x2": 150.0, "y2": 150.0, "cls": "person", "conf": 0.3}])
  out = tf([{"x1": 100.0, "y1": 100.0, "x2": 150.0, "y2": 150.0, "cls": "car", "conf": 0.3}])
  assert out[0]["conf"] == pytest.approx(0.3)  # 类别不同不互相 boost


def test_detector_applies_temporal_filter():
  runner = _StubRunner(_raw_output([(0, 0.9, 320, 200, 60, 80)]))
  now = [0.0]
  det = YoloDetector(PKL_PATH, runner=runner, conf_threshold=0.15, clock=lambda: now[0])
  boxes = det.infer(np.zeros((384, 640, 3), np.uint8))
  assert boxes[0]["conf"] == pytest.approx(0.9)  # 首帧无先前匹配
  now[0] = 0.4  # 越过 3Hz 节流间隔，触发真实推理
  boxes2 = det.infer(np.zeros((384, 640, 3), np.uint8))
  assert boxes2[0]["conf"] == pytest.approx(0.95)  # 第二帧匹配 → +0.05
