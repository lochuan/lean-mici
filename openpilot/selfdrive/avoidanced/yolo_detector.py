"""3Hz YOLO26n BDD7 ROI detector for avoidanced.

Consumes a 640x384 uint8 ROI frame (the model's native input, zero resize) and
produces a list of detections::

    [{"x1": float, "y1": float, "x2": float, "y2": float, "cls": str, "conf": float}, ...]

``cls`` is one of ``person`` / ``rider`` / ``car`` / ``bus`` / ``truck`` /
``bicycle`` / ``motorcycle`` (the BDD7 classes; the planner weights VRUs
person/rider/bicycle/motorcycle above vehicles car/bus/truck). The compiled
tinygrad pkl is committed in ``models/`` (built by
``models/compile_yolo_onnx.py`` on the device, QCOM backend); the trained ONNX
itself stays in the training project.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np

INPUT_H, INPUT_W = 384, 640
DEFAULT_CONF_THRESHOLD = 0.15
DEFAULT_IOU_THRESHOLD = 0.45
DEFAULT_FPS = 3.0

# BDD8 class id -> name. The whole output head is emitted; everything maps to a
# planner weight (VRU vs vehicle) in avoidance_planner.
CLASS_NAMES = {0: "person", 1: "rider", 2: "car", 3: "bus", 4: "truck", 5: "bicycle", 6: "motorcycle", 7: "tricycle"}


class TemporalFilter:
  """Detection-level temporal smoothing for low-confidence distant targets.

  A VRU detected at 0.06-0.15 conf on consecutive frames (common for 20-60m
  targets in the reduced 384x640 crop) flickers in and out. This filter:

  * **confirmation boost**: a detection IoU-matched to last frame's detection of
    the same class gets ``+boost`` conf (capped at 1.0) — repeated sightings of
    an object reinforce it instead of each frame re-deciding on raw conf;
  * **carry-forward**: a previously confirmed detection missing this frame is
    re-emitted for up to ``carry_frames`` with ``conf * carry_decay``, so a
    one-frame dropout (NMS juggling, quantisation noise) does not drop the
    object the planner is mid-manoeuvre on.

  Runs per 3Hz tick in numpy postprocessing; cheap (few detections / tick).
  """

  def __init__(self, boost: float = 0.05, boost_cap: float = 1.0, carry_frames: int = 1,
               carry_decay: float = 0.8, iou_threshold: float = 0.3):
    self.boost = boost
    self.boost_cap = boost_cap
    self.carry_frames = carry_frames
    self.carry_decay = carry_decay
    self.iou_threshold = iou_threshold
    self._prev: list[tuple[dict, int]] = []  # (detection, remaining carry budget)

  def _iou(self, a: dict, b: dict) -> float:
    ax1, ay1, ax2, ay2 = a["x1"], a["y1"], a["x2"], a["y2"]
    bx1, by1, bx2, by2 = b["x1"], b["y1"], b["x2"], b["y2"]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0

  def __call__(self, detections: list[dict]) -> list[dict]:
    prev = self._prev  # [(detection, carry_left)]
    out: list[dict] = []
    emitted: list[tuple[dict, int]] = []
    used: set[int] = set()

    # confirmation boost: current detection matched to last frame's same-class box.
    for d in detections:
      best_i, best_iou = None, self.iou_threshold
      for i, (p, _cl) in enumerate(prev):
        if i in used or p["cls"] != d["cls"]:
          continue
        iou = self._iou(p, d)
        if iou > best_iou:
          best_i, best_iou = i, iou
      if best_i is not None:
        used.add(best_i)
        d["conf"] = min(self.boost_cap, d["conf"] + self.boost)
      out.append(d)
      emitted.append((d, self.carry_frames))

    # carry-forward: unmatched previous detections re-emitted while budget lasts.
    for i, (p, cl) in enumerate(prev):
      if i not in used and cl > 0:
        carried = dict(p)
        carried["conf"] = p["conf"] * self.carry_decay
        out.append(carried)
        emitted.append((carried, cl - 1))

    self._prev = emitted
    out.sort(key=lambda d: d["conf"], reverse=True)
    return out


class Runner(Protocol):
  def run(self, inp: np.ndarray) -> np.ndarray: ...


class TinygradRunner:
  """Loads the committed yolo pkl (our OOB format, see models/compile_yolo_onnx.py)
  and runs the captured TinyJit graph on the QCOM device.

  The captured input is a tensor realized on Device.DEFAULT; TinyJit replays
  only while the input buffer identity is stable, so the runner holds ONE
  persistent input tensor and refreshes its contents with ``assign`` (an
  in-place copy kernel that preserves the buffer). Allocating a fresh tensor
  per frame changes the buffer id and forces a re-lower, which intermittently
  crashes on a symbolic split dim inside upstream tinygrad (device-validated).
  """

  def __init__(self, pkl_path: str | Path):
    from tinygrad import Device, Tensor, dtypes
    from openpilot.selfdrive.modeld.helpers import load_oob
    with open(pkl_path, "rb") as f:
      self._jit = load_oob(f)
    # expected_names mirrors how the JIT was captured: an int is a POSITIONAL
    # arg index, a str is a keyword. compile_yolo_onnx.py captures ``run(x)``
    # positionally, so this is normally [0] -- and ``jit(**{0: t})`` would raise
    # "TypeError: keywords must be strings" (device-validated).
    self._input_name = self._jit.captured.expected_names[0]
    self._positional = isinstance(self._input_name, int)
    # Persistent input buffer: same object every call -> stable buffer id -> JIT replay.
    self._input = Tensor.zeros(1, 3, INPUT_H, INPUT_W, dtype=dtypes.float32, device=Device.DEFAULT).realize()

  def run(self, inp: np.ndarray) -> np.ndarray:
    from tinygrad import Device, Tensor
    self._input.assign(Tensor(np.ascontiguousarray(inp), device=Device.DEFAULT))
    if self._positional:
      return self._jit(self._input).numpy()
    return self._jit(**{self._input_name: self._input}).numpy()


def preprocess(frame: np.ndarray) -> np.ndarray:
  """uint8 (384, 640, 3) RGB ROI -> float32 NCHW (1, 3, 384, 640) in [0, 1]."""
  if frame.shape != (INPUT_H, INPUT_W, 3):
    raise ValueError(f"expected ROI {(INPUT_H, INPUT_W, 3)}, got {frame.shape}")
  chw = np.ascontiguousarray(frame.transpose(2, 0, 1), dtype=np.float32) / 255.0
  return chw[np.newaxis, ...]


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
  order = scores.argsort()[::-1]
  keep: list[int] = []
  while order.size > 0:
    i = int(order[0])
    keep.append(i)
    if order.size == 1:
      break
    rest = order[1:]
    xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
    yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
    xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
    yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
    inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
    area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
    area_rest = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
    iou = inter / np.maximum(area_i + area_rest - inter, 1e-9)
    order = rest[iou <= iou_threshold]
  return keep


def postprocess(raw: np.ndarray, conf_threshold: float = DEFAULT_CONF_THRESHOLD,
                iou_threshold: float = DEFAULT_IOU_THRESHOLD,
                class_names: dict[int, str] = CLASS_NAMES) -> list[dict]:
  """Decode YOLOv8 detect head output (1, 4 + num_classes, num_anchors).

  YOLOv8 always emits channel-major (4 box + num_classes rows, one column per
  anchor), so transpose unconditionally to (num_anchors, 4 + num_classes).
  """
  pred = raw[0].T
  if pred.size == 0:
    return []

  xywh = pred[:, :4]
  class_scores = pred[:, 4:]
  cls_ids = class_scores.argmax(axis=1)
  confs = class_scores.max(axis=1)

  keep_mask = np.isin(cls_ids, list(class_names)) & (confs >= conf_threshold)
  if not keep_mask.any():
    return []

  boxes = np.empty((int(keep_mask.sum()), 4), dtype=np.float32)
  xywh = xywh[keep_mask]
  boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2.0
  boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2.0
  boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2.0
  boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2.0
  boxes[:, 0::2] = np.clip(boxes[:, 0::2], 0.0, INPUT_W)
  boxes[:, 1::2] = np.clip(boxes[:, 1::2], 0.0, INPUT_H)
  confs = confs[keep_mask]
  cls_ids = cls_ids[keep_mask]

  detections: list[dict] = []
  for cls_id in np.unique(cls_ids):
    idx = np.flatnonzero(cls_ids == cls_id)
    for i in _nms(boxes[idx], confs[idx], iou_threshold):
      box = boxes[idx[i]]
      detections.append({
        "x1": float(box[0]),
        "y1": float(box[1]),
        "x2": float(box[2]),
        "y2": float(box[3]),
        "cls": class_names[int(cls_id)],
        "conf": float(confs[idx[i]]),
      })
  detections.sort(key=lambda d: d["conf"], reverse=True)
  return detections


class YoloDetector:
  """3Hz YOLO detector. ``runner``/``params``/``clock`` are injectable for tests."""

  def __init__(self, pkl_path: str | Path, conf_threshold: float | None = None,
               iou_threshold: float = DEFAULT_IOU_THRESHOLD, fps: float = DEFAULT_FPS,
               runner: Runner | None = None, params=None, clock: Callable[[], float] = time.monotonic,
               temporal: TemporalFilter | None = None):
    self.pkl_path = Path(pkl_path)
    self.conf_threshold = conf_threshold
    self.iou_threshold = iou_threshold
    self.min_interval_s = 1.0 / fps if fps > 0 else 0.0
    self._runner = runner
    self._params = params
    self._clock = clock
    self._temporal = temporal if temporal is not None else TemporalFilter()
    self._last_run_t: float | None = None
    self._last_detections: list[dict] = []

  def _ensure_runner(self) -> Runner:
    if self._runner is None:
      self._runner = TinygradRunner(self.pkl_path)
    return self._runner

  def _confidence(self) -> float:
    if self.conf_threshold is not None:
      return self.conf_threshold
    if self._params is None:
      try:
        from openpilot.common.params import Params
        self._params = Params()
      except Exception:
        return DEFAULT_CONF_THRESHOLD
    try:
      value = self._params.get("AvoidanceMinConfidence")
    except Exception:
      return DEFAULT_CONF_THRESHOLD
    if value is None:
      return DEFAULT_CONF_THRESHOLD
    return float(np.clip(float(value), 0.0, 1.0))

  @property
  def last_detections(self) -> list[dict]:
    return self._last_detections

  def infer(self, frame: np.ndarray, now: float | None = None) -> list[dict]:
    """Run the detector on a 384x640 uint8 ROI, throttled to ``fps``."""
    now = self._clock() if now is None else now
    if self.min_interval_s > 0 and self._last_run_t is not None and (now - self._last_run_t) < self.min_interval_s:
      return self._last_detections
    raw = self._ensure_runner().run(preprocess(frame))
    detections = postprocess(raw, conf_threshold=self._confidence(), iou_threshold=self.iou_threshold)
    detections = self._temporal(detections)
    self._last_run_t = now
    self._last_detections = detections
    return detections
