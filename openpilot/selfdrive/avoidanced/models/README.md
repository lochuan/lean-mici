# avoidanced YOLO model

YOLO26n (BDD7: person / rider / car / bus / truck / bicycle / motorcycle) runs
on a 640x384 (WxH) ROI at 3Hz to classify VRUs and vehicles for lateral
avoidance. It is separate from `modeld`'s supercombo (modeld's input contract
is hard-bound to supercombo), so avoidanced ships its own pkl.

**The compiled pkl (`yolo_tinygrad.pkl`) IS committed**: the device receives it
via git — no on-device compile, no weights dance. The trained ONNX lives in the
training project (`~/Projects/yolo26n-bdd100k`), not here.

## Pipeline

```
训练 (服务器)          yolo train model=yolo26n.pt data=<bdd>.yaml imgsz=640...
    ↓ best.pt
导出 (服务器/本地)     yolo export model=best.pt format=onnx int8=True imgsz=384,640 data=<bdd>.yaml
    ↓ *.onnx          (default one-to-many head; do NOT use nms=False — slower on QCOM)
编译 (设备, compile_yolo_onnx.py)
    ↓ ONNX → OnnxRunner → TinyJit(prune) → dump_oob   [our OOB format]
运行 (avoidanced)      TinygradRunner: load_oob + persistent QCOM input buffer + assign per frame
```

## Regenerating the pkl (when the trained model changes)

Run **on the comma device** (12V required — mici powers down CPU 4-7 otherwise,
the compile needs CPU 4). Flags mirror `modeld/SConscript`; `PICKLE_OOB=1`
selects openpilot's OOB pickle format (read back by modeld's `load_oob`):

```bash
PATH=/usr/local/venv/bin:$PATH PARALLEL=0 DEV=QCOM FLOAT16=1 NOLOCALS=1 \
  JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 PICKLE_OOB=1 \
  PYTHONPATH=<new-tinygrad-tree>:/data/openpilot \
  python3 openpilot/selfdrive/avoidanced/models/compile_yolo_onnx.py \
  <model.onnx> openpilot/selfdrive/avoidanced/models/yolo_tinygrad.pkl
```

Do NOT use tinygrad's `examples/openpilot/compile_onnx.py` artifact: its
captured `run` re-lowers through OnnxRunner whenever the input buffer identity
changes, which intermittently crashes on a symbolic split dim with this QAT
ONNX (device-validated 2026-09-18). Our format captures a concrete-shape
TinyJit; 60s soak: 221 iters / 0 fails, p50 273ms.

DEV=CPU works for a local smoke test (no QCOM backend needed).

## Detector contract

`YoloDetector.infer(frame uint8 (384, 640, 3)) -> list[dict]`, 3Hz throttled:

- `cls` ∈ BDD7; `conf` comes from `AvoidanceMinConfidence` (default 0.4),
  NMS IoU 0.45 per class.
- Planner weights (avoidance_planner): VRU person/rider/bicycle/motorcycle = 1.0,
  vehicles car/bus/truck = 0.6.

## Latency budget

Device-measured p50 **273ms** @ 640x384 (QCOM, shared GPU with modeld, DM
killed). 3Hz budget is 333ms — fits with headroom. The 5Hz design was
revised: latency is dominated by fixed per-run cost (kernel dispatch through
the Adreno driver), not model FLOPs — smaller resolutions barely move it.

## Unit tests

The QCOM-compiled pkl runs only on comma hardware; detector interface tests
use injected stub runners and skip on PC (`skipif(PC)`).