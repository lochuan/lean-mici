# avoidanced YOLO model

YOLOv8n-det runs on a 640x384 (WxH) lower-ROI frame at 5Hz to classify VRUs
(person / bicycle / motorcycle) and cars for lateral avoidance. It is a separate
model from `modeld`'s supercombo: `modeld`'s input contract is hard-bound to
supercombo, so avoidanced ships its own pkl.

**Weights and ONNX are not stored in the repo.** Only the code and build recipe
live here. `*.onnx` and `*.pkl` in this directory are gitignored.

## 1. Export the ONNX (one-off; any machine — Mac/uv verified)

Per the lean-mici build split (see KB 发版流程), the pkl is device-only (QCOM
tinygrad JIT); the ONNX itself is a plain file with no ABI coupling, so export
wherever convenient and scp it to the device. Export **fp32** (no `half=True`):
CPU torch cannot export fp16 — it fails on the comma 4 and on the Mac alike —
and FP16 happens anyway at the tinygrad compile step (`FLOAT16=1` below).
Verified end-to-end on macOS arm64 (uv venv + ultralytics + onnx): input
`(1, 3, 384, 640)`, output `(1, 84, 5040)`, `DEV=CPU` compile and live
inference through `YoloDetector` both pass (bus.jpg → 4 persons).

```bash
# with uv (ephemeral venv, delete afterwards)
uv venv /tmp/yolo-export && VIRTUAL_ENV=/tmp/yolo-export uv pip install ultralytics onnx
cd /tmp && /tmp/yolo-export/bin/yolo export model=yolov8n.pt format=onnx imgsz=384,640 opset=12
mv /tmp/yolov8n.onnx <repo>/openpilot/selfdrive/avoidanced/models/yolov8n-det-640x384.onnx
rm -rf /tmp/yolo-export /tmp/yolo-export-work

# then to the device (ONNX is gitignored, it does not travel via git)
scp openpilot/selfdrive/avoidanced/models/yolov8n-det-640x384.onnx \
    comma@<device>:/data/openpilot/openpilot/selfdrive/avoidanced/models/
```

Plain `python3 -m venv` + `pip install ultralytics onnx` works the same way on
the device itself; `imgsz=384,640` is H,W (verified: input comes out
`(1,3,384,640)`).

The ONNX input must be `(1, 3, 384, 640)` float32 and the detect head
output `(1, 4 + 80, N)` (YOLOv8 has no objectness head).

## 2. Compile to tinygrad pkl

Run on the comma 4 (mici), **on 12V** (mici powers down CPU 4-7 otherwise, and
the compile needs CPU 4). The script sets
`DEV=QCOM FLOAT16=1 IMAGE=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1`
(aligned with `modeld/SConscript`) plus `PICKLE_OOB=1`, and drives
`tinygrad_repo/examples/openpilot/compile3.py`:

```bash
openpilot/selfdrive/avoidanced/models/compile_yolo.sh
# -> models/yolo_tinygrad.pkl
```

Local CPU smoke test (no QCOM backend): `DEV=CPU openpilot/selfdrive/avoidanced/models/compile_yolo.sh`.

`PICKLE_OOB=1` makes `compile3.py` write the out-of-band pickle format that
`TinygradRunner` reads back with modeld's `load_oob()` (same format as the
`compile_modeld.py` + `dump_oob` modeld pkls). Without it the pkl is a plain
pickle and `load_oob` fails.

`modeld/SConscript` is intentionally not modified: the ONNX is not in the repo,
so a build-graph entry would fail clean builds. The standalone script is the
"independent script" option from the plan.

## 3. Use

```python
from openpilot.selfdrive.avoidanced.yolo_detector import YoloDetector

det = YoloDetector("openpilot/selfdrive/avoidanced/models/yolo_tinygrad.pkl")
detections = det.infer(roi_384x640_rgb_uint8)  # 5Hz throttled
# [{"x1": .., "y1": .., "x2": .., "y2": .., "cls": "person", "conf": 0.71}, ...]
```

- `cls` is one of `person` / `bicycle` / `car` / `motorcycle`; other COCO classes
  are dropped.
- Boxes are in ROI pixel coordinates (0..640, 0..384), xyxy, clipped to the ROI.
- Confidence threshold comes from the `AvoidanceMinConfidence` param; when unset
  the detector falls back to `DEFAULT_CONF_THRESHOLD` (0.4). NMS IoU 0.45,
  per class.
- `infer()` is throttled to 5Hz (`fps` arg); a faster call returns the cached
  detections.

## 4. Latency budget

Target **< 120ms/frame** on the comma 4 (mici) (5Hz = 200ms budget), leaving
headroom for the modeld process on the isolated cores. Measure after building
the pkl:

```bash
DEV=QCOM .venv/bin/python - <<'PY'
import time, numpy as np
from openpilot.selfdrive.avoidanced.yolo_detector import YoloDetector
d = YoloDetector("openpilot/selfdrive/avoidanced/models/yolo_tinygrad.pkl", fps=0)
f = np.zeros((384, 640, 3), np.uint8)
d.infer(f)
ts = []
for _ in range(20):
  t = time.monotonic(); d.infer(f, now=0.0); ts.append((time.monotonic() - t) * 1e3)
print(f"min {min(ts):.1f} ms  median {sorted(ts)[len(ts)//2]:.1f} ms")
PY
```

Measured on device: **pending** (no weights/device in the authoring environment).
The unit tests run the real pre/post pipeline with an injected runner and skip
the pkl test when the pkl is absent.
