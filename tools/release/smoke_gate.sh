#!/usr/bin/env bash
# smoke_gate.sh — run the on-device onroad smoke test and fail loudly.
#
# This is a release gate. The 2026-09-09 in-car failure ("sunnypilot
# Unavailable / Waiting to start") shipped even though the smoke test had
# already reported SMOKE: FAIL with "selfdriveState msgs: 0" — the result was
# simply never checked. Treat a FAIL here as a blocked release.
#
# Usage:
#   ./tools/release/smoke_gate.sh [duration_seconds]
#
# Environment:
#   DEVICE  comma device SSH target (default comma@10.223.134.33)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
DEVICE="${DEVICE:-comma@10.223.134.33}"
DURATION="${1:-60}"
REMOTE_PATH="/data/smoke_onroad_device.py"

echo "[-] copying smoke harness to device"
scp -q -o BatchMode=yes "$DIR/smoke_onroad_device.py" "$DEVICE:$REMOTE_PATH"

echo "[-] running onroad smoke test (${DURATION}s); this stops any running openpilot"
set +e
ssh -o BatchMode=yes "$DEVICE" \
  "cd /data/openpilot && sudo systemctl stop comma 2>/dev/null; \
   PYTHONPATH=/data/openpilot:/data/openpilot/openpilot \
   /usr/local/venv/bin/python $REMOTE_PATH $DURATION" 2>&1 | tee /tmp/smoke_gate.log
rc=${PIPESTATUS[0]}
set -e

if [ "$rc" -ne 0 ] || ! grep -q "SMOKE: PASS" /tmp/smoke_gate.log; then
  cat >&2 <<'BANNER'

********************************************************************************
*  ❌  SMOKE GATE FAILED — do not ship this build.
*
*  The device could not complete a clean onroad startup. Check /tmp/smoke_gate.log
*  for the crashed process and its exit code, then fix before releasing.
********************************************************************************
BANNER
  exit 1
fi

echo "[ok] SMOKE PASS"
