"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.modeld_v2.vru_decode import (
  nms, decode_detections, ALLOWED_CLASSES, NUM_CLASSES,
)

MODEL_H, MODEL_W = 192, 320
N_ANCHORS = 1260  # 960 + 240 + 60


def plant_output(cx: float, cy: float, w: float, h: float,
                 class_id: int, score: float = 0.9) -> np.ndarray:
  """Raw (1, 84, N) model output with one planted detection at anchor 0.

  The exported graph outputs rows [0:4] as box (cx, cy, w, h) in model-input
  pixels and rows [4:84] as class probabilities (sigmoid applied in-graph)."""
  y = np.zeros((4 + NUM_CLASSES, N_ANCHORS), dtype=np.float32)
  y[0, 0], y[1, 0], y[2, 0], y[3, 0] = cx, cy, w, h
  y[4 + class_id, 0] = score
  return y[None]


class TestDecode(OpenpilotTestCase):
  def test_nms(self):
    boxes = np.array([[0., 0., 10., 10.], [1., 1., 11., 11.], [50., 50., 60., 60.]])
    scores = np.array([0.9, 0.8, 0.7])
    assert nms(boxes, scores, 0.45) == [0, 2]

  def test_decode_planted_box(self):
    # anchor 0: cx=10, cy=14, w=20, h=28 -> xyxy (0, 0, 20, 28)
    out = plant_output(10.0, 14.0, 20.0, 28.0, class_id=7, score=0.9)
    dets = decode_detections(out, MODEL_H, MODEL_W, ALLOWED_CLASSES)
    assert len(dets) == 1
    d = dets[0]
    assert d['classId'] == 7
    np.testing.assert_allclose([d['x1'], d['y1'], d['x2'], d['y2']], [0.0, 0.0, 20.0, 28.0], atol=1e-5)
    np.testing.assert_allclose(d['score'], 0.9, atol=1e-6)

  def test_conf_threshold(self):
    out = plant_output(10.0, 14.0, 20.0, 28.0, class_id=7, score=0.27)  # > 0.25
    assert len(decode_detections(out, MODEL_H, MODEL_W)) == 1
    out = plant_output(10.0, 14.0, 20.0, 28.0, class_id=7, score=0.12)  # < 0.25
    assert len(decode_detections(out, MODEL_H, MODEL_W)) == 0

  def test_class_filter(self):
    out = plant_output(10.0, 14.0, 20.0, 28.0, class_id=4)  # airplane, not allowed
    assert len(decode_detections(out, MODEL_H, MODEL_W)) == 0

  def test_overlapping_suppressed(self):
    # two anchors with identical boxes -> full overlap -> only higher score survives
    y = np.zeros((4 + NUM_CLASSES, N_ANCHORS), dtype=np.float32)
    for ai, score in ((0, 0.90), (1, 0.95)):
      y[:4, ai] = (10.0, 14.0, 20.0, 28.0)
      y[4 + 2, ai] = score  # car
    dets = decode_detections(y[None], MODEL_H, MODEL_W)
    assert len(dets) == 1 and dets[0]['classId'] == 2
    assert dets[0]['score'] > 0.9

  def test_clipping(self):
    # box extending beyond model edges gets clipped to [0, W] x [0, H]
    out = plant_output(10.0, 190.0, 40.0, 40.0, class_id=1, score=0.9)
    dets = decode_detections(out, MODEL_H, MODEL_W)
    assert len(dets) == 1
    np.testing.assert_allclose([dets[0]['x1'], dets[0]['y1'], dets[0]['x2'], dets[0]['y2']],
                               [0.0, 170.0, 30.0, 192.0], atol=1e-5)

  def test_2d_output_shape(self):
    out = plant_output(10.0, 14.0, 20.0, 28.0, class_id=1, score=0.9)[0]  # (84, N)
    assert len(decode_detections(out, MODEL_H, MODEL_W)) == 1
