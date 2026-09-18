import time
from pathlib import Path

import numpy as np
import pytest

from openpilot.selfdrive.avoidanced.yolo_detector import (
  CLASS_NAMES,
  YoloDetector,
  postprocess,
  preprocess,
)

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
PKL_PATH = MODELS_DIR / "yolo_tinygrad.pkl"

# YOLOv8 detect head emits (1, 4 + num_coco_classes, num_anchors); COCO has 80 classes.
NUM_CLASSES = 80
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


# --- brief Step 1: on-device interface test (weights excluded from the repo) ---

@pytest.mark.skipif(not PKL_PATH.exists(), reason="yolo_tinygrad.pkl not built; weights are not stored in the repo")
def test_detector_returns_boxes():
  det = YoloDetector(str(PKL_PATH))
  boxes = det.infer(np.zeros((384, 640, 3), np.uint8))
  assert isinstance(boxes, list)


@pytest.mark.skipif(not PKL_PATH.exists(), reason="yolo_tinygrad.pkl not built; weights are not stored in the repo")
def test_detector_latency_under_120ms():
  det = YoloDetector(str(PKL_PATH), fps=0)
  frame = np.zeros((384, 640, 3), np.uint8)
  det.infer(frame)
  start = time.monotonic()
  det.infer(frame)
  assert (time.monotonic() - start) < 0.120


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
  det = postprocess(raw, conf_threshold=0.4)[0]
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
  dets = postprocess(raw, conf_threshold=0.4)
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
  ])
  assert {d["cls"] for d in postprocess(raw, conf_threshold=0.4)} == set(CLASS_NAMES.values())


def test_postprocess_filters_low_confidence():
  raw = _raw_output([(0, 0.9, 100, 100, 50, 50), (2, 0.2, 300, 100, 60, 60)])
  dets = postprocess(raw, conf_threshold=0.4)
  assert len(dets) == 1
  assert dets[0]["cls"] == "person"


def test_postprocess_drops_classes_outside_allowlist():
  assert postprocess(_raw_output([(5, 0.9, 100, 100, 50, 50)]), conf_threshold=0.4) == []


def test_postprocess_clips_boxes_to_roi():
  raw = _raw_output([(0, 0.9, 5, 5, 40, 40)])
  det = postprocess(raw, conf_threshold=0.4)[0]
  assert det["x1"] == 0.0
  assert det["y1"] == 0.0


def test_postprocess_nms_suppresses_overlapping_same_class():
  raw = _raw_output([(0, 0.9, 100, 100, 50, 50), (0, 0.8, 102, 100, 50, 50)])
  dets = postprocess(raw, conf_threshold=0.4, iou_threshold=0.45)
  assert len(dets) == 1
  assert dets[0]["conf"] == pytest.approx(0.9)


def test_postprocess_nms_is_per_class():
  raw = _raw_output([(0, 0.9, 100, 100, 50, 50), (2, 0.8, 102, 100, 50, 50)])
  assert len(postprocess(raw, conf_threshold=0.4, iou_threshold=0.45)) == 2


def test_postprocess_empty_output():
  assert postprocess(np.zeros((1, 4 + NUM_CLASSES, 0), np.float32), conf_threshold=0.4) == []


# --- detector shell ---

def test_detector_returns_boxes_with_injected_runner():
  runner = _StubRunner(_raw_output([(0, 0.9, 320, 200, 60, 80)]))
  det = YoloDetector(PKL_PATH, runner=runner, conf_threshold=0.4)
  boxes = det.infer(np.zeros((384, 640, 3), np.uint8))
  assert isinstance(boxes, list)
  assert boxes[0]["cls"] == "person"
  assert runner.calls == 1


def test_detector_throttles_to_5hz():
  runner = _StubRunner(_raw_output([(0, 0.9, 320, 200, 60, 80)]))
  now = [0.0]
  det = YoloDetector(PKL_PATH, runner=runner, conf_threshold=0.4, fps=5.0, clock=lambda: now[0])
  frame = np.zeros((384, 640, 3), np.uint8)
  det.infer(frame)
  det.infer(frame)
  assert runner.calls == 1
  now[0] = 0.1
  det.infer(frame)
  assert runner.calls == 1
  now[0] = 0.2
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
