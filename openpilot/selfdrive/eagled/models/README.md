# eagled YOLO model

YOLO26n (8 classes: person / rider / car / bus / truck / bicycle / motorcycle /
**tricycle**) runs on a 384x640 (HxW) ROI at 3Hz to classify VRUs and vehicles
for lateral avoidance. It is separate from `modeld`'s supercombo (modeld's
input contract is hard-bound to supercombo), so eagled ships its own pkl.

**The compiled pkl (`yolo_tinygrad.pkl`) is NOT committed** — it embeds tinygrad
JIT kernels for one exact tinygrad revision (positional pickle contract), so it
goes stale on every tinygrad bump. It is compiled **on-device at first boot** by
`../SConscript` from the committed ONNX; `tinygrad_repo/**` is an scons
dependency, so any pin bump recompiles it automatically. The runtime
(`yolo_detector.TinygradRunner`) refuses to load a pkl whose
`<pkl>.tinygrad_pin` sidecar disagrees with the running tree; eagled then
degrades to radar-only (logged once, never crashes the daemon).

**The ONNX (`yolo26n-bdd7-fp32-384x640.onnx`) IS committed**: weights are
pin-stable. It is the FP32 384x640 export from the training repo
(`lochuan/yolo26n-bdd100k`, "comma openpilot dedicated" listing). Provenance
check (2026-09-21): 105/223 initializers byte-match the previous committed
pkl's out-of-band buffers — the INT8 export does not (3/612). Note the repo's
older docs say 7 classes; the actual ONNX (and this pkl) are 8-class — the
extra class is `tricycle` (head = 4 box + 8 cls = 12 channels).

## Pipeline

```
训练 (服务器)          yolo train model=yolo26n.pt data=<bdd>.yaml imgsz=640...
    ↓ best.pt
导出 (服务器/本地)     yolo export model=best.pt format=onnx imgsz=384,640   (fp32; head 4+8)
    ↓ *.onnx          → commit to models/
编译 (设备, 首启自动)   ../SConscript: ONNX → OnnxRunner → TinyJit(prune) → dump_oob
    ↓ yolo_tinygrad.pkl (+ .tinygrad_pin sidecar)   [our OOB format]
运行 (eagled)      TinygradRunner: load_oob + persistent QCOM input buffer + assign per frame
```

## Regenerating the pkl by hand (rarely needed; first boot does it)

Run **on the comma device**. `manager/build.py` wakes CPUs 4-7 via
`HARDWARE.set_power_save(False)` before scons, so the normal first-boot path is
fine; for the manual path check CPU4 is online first
(`cat /sys/devices/system/cpu/cpu4/online`, else
`echo 1 | sudo tee /sys/devices/system/cpu/cpu4/online`).

```bash
cd /data/safe_staging/merged   # any cwd on /data that is NOT /data/openpilot
  # (the tree's tinygrad symlink shadows sys.path from that cwd — see warning)
PATH=/usr/local/venv/bin:$PATH PARALLEL=0 DEV=QCOM:IR3 IMAGE=1 FLOAT16=1 \
  JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 \
  PYTHONPATH=/data/openpilot:/data/openpilot/tinygrad_repo \
  python3 /data/openpilot/openpilot/selfdrive/eagled/models/compile_yolo_onnx.py \
  /data/openpilot/openpilot/selfdrive/eagled/models/yolo26n-bdd7-fp32-384x640.onnx \
  /data/openpilot/openpilot/selfdrive/eagled/models/yolo_tinygrad.pkl
```

Why `QCOM:IR3` (mesa NIR -> freedreno ir3) instead of the default `QCOM:CL`:
the Qualcomm OpenCL LLVM blob aborts on this model's conv kernels ("Custom
lowering code for this instruction is not implemented yet: 150"), which is why
IMAGE=1 was long believed impossible here. IR3 has no such limit and supports
fp16 unconditionally (`QCOMCLRenderer` gates half behind `IMAGE && FLOAT16`).
Requires `tinymesa` (in AGNOS's venv; tinygrad pins `tinymesa==25.2.7.2`). IR3
is gated to a630 only. Measured on comma 4 (mici, Adreno 630), 384x640:

```
QCOM:CL, no IMAGE (old)    290.6 ms p50   out (1, 11, 5040)   <- old 7-class pkl
QCOM:IR3 + IMAGE=1 + fp16   67.9 ms p50   out (1, 12, 5040)   <- 8-class
```

Numerically validated against the tinygrad CPU backend on road frames:
identical detection counts, 17/17 class match, box IoU >= 0.989, confidence
delta <= 0.013.

The compile writes a `<pkl>.tinygrad_pin` sidecar (the tinygrad revision the
kernels were built against); the runtime refuses a mismatched sidecar, so a
stale manual compile cannot silently load against a newer tree.

WARNING: run from a directory OTHER than /data/openpilot. That tree contains a
`tinygrad -> tinygrad_repo/tinygrad` symlink, and python puts the cwd (for
`-c`) or the script dir ahead of PYTHONPATH, so a stale in-tree tinygrad can
silently shadow the one you selected -- which then fails to load the pkl with
"CallInfo.__init__() takes from 1 to 6 positional arguments but 7 were given".

Do NOT pass `NOLOCALS=1`: it is a no-op in this tinygrad tree.

DEV=CPU works for a local smoke test (no QCOM backend).

## Detector contract

`YoloDetector.infer(frame uint8 (384, 640, 3)) -> list[dict]`, 3Hz throttled:

- `cls` ∈ the 8 classes above; `conf` comes from `AvoidanceMinConfidence`
  (default 0.4), NMS IoU 0.45 per class.
- Planner weights (avoidance_planner): VRU person/rider/bicycle/motorcycle/tricycle = 1.0,
  vehicles car/bus/truck = 0.6.

## Latency budget

Device-measured p50 **273ms** @ 640x384 (QCOM, shared GPU with modeld, DM
killed). 3Hz budget is 333ms — fits with headroom. The 5Hz design was
revised: latency is dominated by fixed per-run cost (kernel dispatch through
the Adreno driver), not model FLOPs — smaller resolutions barely move it.
With the IR3/IMAGE/fp16 path the GPU kernel itself is 68ms p50 (85ms
end-to-end incl. preprocess + NMS) — see the timing table above.

## Unit tests

The QCOM-compiled pkl runs only on comma hardware; detector interface tests
use injected stub runners and skip on PC (`skipif(PC)`).
