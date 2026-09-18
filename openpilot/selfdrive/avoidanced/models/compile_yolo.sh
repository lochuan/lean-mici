#!/usr/bin/env bash
# Compile the YOLOv8n-det ROI ONNX into a tinygrad pkl for avoidanced.
#
# Usage:
#   ./compile_yolo.sh [input.onnx] [output.pkl]
#
# Defaults:
#   input  = models/yolov8n-det-640x384-fp16.onnx
#   output = models/yolo_tinygrad.pkl
#
# On a comma 3X this MUST run on 12V: mici keeps CPU 4-7 powered down otherwise
# and the tinygrad compile needs CPU 4 (see modeld/SConscript for the same note).
# For a local CPU sanity check (no QCOM):  DEV=CPU ./compile_yolo.sh
#
# Weights are not stored in the repo; export the ONNX first (see README.md).

set -euo pipefail

MODELS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$MODELS_DIR" rev-parse --show-toplevel)"

ONNX="${1:-$MODELS_DIR/yolov8n-det-640x384-fp16.onnx}"
OUT="${2:-$MODELS_DIR/yolo_tinygrad.pkl}"

if [[ ! -f "$ONNX" ]]; then
  echo "ONNX not found: $ONNX" >&2
  echo "Export yolov8n-det first (see models/README.md); weights are not stored in the repo." >&2
  exit 1
fi

# QCOM is the deployment target; allow DEV=CPU for a local smoke test.
export DEV="${DEV:-QCOM}"
if [[ "$DEV" == "QCOM" ]]; then
  export FLOAT16=1 IMAGE=1 NOLOCALS=1 JIT_BATCH_SIZE=0
fi
export PYTHONPATH="${PYTHONPATH:-}:$ROOT/tinygrad_repo"

echo "compiling $ONNX -> $OUT (DEV=$DEV)"
exec python3 "$ROOT/tinygrad_repo/examples/openpilot/compile3.py" "$ONNX" "$OUT"
