#!/usr/bin/env python3
"""Compile a YOLO ONNX into a tinygrad pkl in openpilot's own OOB format.

Mirrors ``modeld/compile_modeld.py`` (the production path used for the driving
model): ONNX -> OnnxRunner -> TinyJit(prune) -> ``dump_oob``. The saved pkl is
loaded back by ``yolo_detector.TinygradRunner`` with modeld's ``load_oob``; it
does NOT use tinygrad's examples ``compile_onnx.py`` artifact format.

Why not upstream's compile_onnx.py: its captured ``run`` re-lowers through the
OnnxRunner whenever input-buffer identity changes, and on this QAT ONNX that
intermittently rebuilds a graph with a symbolic split dim (crash). Our format
captures a plain TinyJit with concrete shapes; modeld runs the same path at
20Hz on this device without issues.

Usage (run ON the device, on 12V; QCOM is the deployment target):

    PYTHONPATH=/data/tinygrad_upstream PARALLEL=0 DEV=QCOM FLOAT16=1 NOLOCALS=1 \
        JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 PICKLE_OOB=1 \
        python3 compile_yolo_onnx.py model.onnx model.pkl [--input-name images]

DEV=CPU can be used for a local smoke test (no QCOM backend).
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from tinygrad import Context, Device, Tensor
from tinygrad.engine.jit import TinyJit
from tinygrad.nn.onnx import OnnxRunner

from openpilot.selfdrive.modeld.helpers import dump_oob


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("onnx", type=Path)
  parser.add_argument("output", type=Path)
  parser.add_argument("--input-name", default="images")
  parser.add_argument("--output-name", default="output0")
  args = parser.parse_args()

  runner = OnnxRunner(str(args.onnx))
  graph_inputs = runner.graph_inputs
  if args.input_name not in graph_inputs:
    print(f"input {args.input_name!r} not in graph inputs {list(graph_inputs)}", file=sys.stderr)
    return 1
  spec = graph_inputs[args.input_name]
  print(f"input {args.input_name} {spec.shape} {spec.dtype}")

  # Capture input realized on the deployment device (QCOM on-device, CPU here).
  # TinyJit._prepare_jit_inputs calls .realize() on the input; a device-NPY
  # tensor has no renderer, so the input must live on Device.DEFAULT.
  inp = Tensor.zeros(*spec.shape, dtype=spec.dtype, device=Device.DEFAULT).realize()

  @TinyJit(prune=True)
  def run(x: Tensor) -> Tensor:
    return next(iter(runner({args.input_name: x}).values()))

  # Warm up / capture, then validate the JIT reproduces the eager output.
  with Context(DEBUG=0):
    out = run(inp)
    Device[Device.DEFAULT].synchronize()
    expected = np.array(out.numpy(), copy=True)
  for _ in range(2):
    np.testing.assert_array_equal(run(inp).numpy(), expected)

  args.output.parent.mkdir(parents=True, exist_ok=True)
  with open(args.output, "wb") as f:
    dump_oob(run, f)
  print(f"wrote {args.output} ({args.output.stat().st_size/1e6:.1f} MB) inputs={spec.shape}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
