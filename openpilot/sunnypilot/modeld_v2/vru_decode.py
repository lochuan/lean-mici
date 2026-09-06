"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

NUM_CLASSES = 80
CONF_THRESHOLD = 0.25
IOU_THRESHOLD = 0.45
MAX_DETECTIONS = 50

VRU_CLASSES = (0, 1, 3)      # person, bicycle, motorcycle
TRUCK_CLASSES = (5, 7)       # bus, truck
CAR_CLASSES = (2,)           # car
ALLOWED_CLASSES = VRU_CLASSES + TRUCK_CLASSES + CAR_CLASSES


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
  order = np.argsort(-scores)
  keep = []
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
    inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
    area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
    area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
    iou = inter / np.maximum(area_i + area_r - inter, 1e-9)
    order = rest[iou <= iou_threshold]
  return keep


def decode_detections(output: np.ndarray, model_h: int, model_w: int,
                      allowed_classes=ALLOWED_CLASSES, conf_threshold=CONF_THRESHOLD,
                      iou_threshold=IOU_THRESHOLD, max_det=MAX_DETECTIONS) -> list[dict]:
  """Decode YOLOv8 export output into filtered detections.

  The exported graph already applies DFL decoding and stride multiplication:
  rows [0:4] are box (cx, cy, w, h) in model-input pixels, rows [4:] are class
  probabilities (sigmoid applied in-graph). Returns xyxy boxes clipped to
  [0, model_w] x [0, model_h]."""
  y = output[0] if output.ndim == 3 else output
  assert y.shape[0] == 4 + NUM_CLASSES, f"unexpected output shape {y.shape}"

  cx, cy, bw, bh = (y[i].astype(np.float64) for i in range(4))
  cls_scores = y[4:].astype(np.float64)  # already sigmoided by the model graph

  class_ids = np.argmax(cls_scores, axis=0)
  scores = cls_scores[class_ids, np.arange(y.shape[1])]

  mask = (scores > conf_threshold) & np.isin(class_ids, allowed_classes)
  if not mask.any():
    return []

  boxes = np.stack([cx[mask] - bw[mask] / 2, cy[mask] - bh[mask] / 2,
                    cx[mask] + bw[mask] / 2, cy[mask] + bh[mask] / 2], axis=1)
  boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0.0, model_w)
  boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0.0, model_h)
  sel_scores = scores[mask]
  sel_classes = class_ids[mask]

  if sel_scores.size > max_det:
    top = np.argsort(-sel_scores)[:max_det]
    boxes, sel_scores, sel_classes = boxes[top], sel_scores[top], sel_classes[top]

  keep = nms(boxes, sel_scores, iou_threshold)
  return [{'classId': int(sel_classes[i]), 'score': float(sel_scores[i]),
           'x1': float(boxes[i, 0]), 'y1': float(boxes[i, 1]),
           'x2': float(boxes[i, 2]), 'y2': float(boxes[i, 3])} for i in keep]
