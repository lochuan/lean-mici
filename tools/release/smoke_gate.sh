#!/usr/bin/env bash
# smoke_gate.sh — run the on-device onroad smoke test and fail loudly.
#
# This is a release gate. The 2026-09-09 in-car failure ("sunnypilot
# Unavailable / Waiting to start") shipped even though the smoke test had
# already reported SMOKE: FAIL with "selfdriveState msgs: 0" — the result was
# simply never checked. Treat a FAIL here as a blocked release.
#
# The gate runs against whatever is checked out at /data/openpilot. It does NOT
# update the device, so unless the device is already on the commit being gated
# it validates the *previous* release — the same "gate reports PASS for the
# wrong artifact" failure this gate exists to prevent. Pass EXPECT_COMMIT to
# make that a hard error instead of a silent mismatch.
#
# Usage:
#   ./tools/release/smoke_gate.sh [duration_seconds]
#
# Environment:
#   DEVICE         comma device SSH target (default comma@10.0.0.27)
#   EXPECT_COMMIT  require the device to be on this commit before gating
#   SKIP_PKL_CHECK=1  skip the shipped-pkl load check (not recommended)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
DEVICE="${DEVICE:-comma@10.0.0.27}"
DURATION="${1:-60}"
REMOTE_PATH="/data/smoke_onroad_device.py"
REMOTE_PKL_CHECK="/data/check_device_pkls.py"

# --- the device must be on the commit we are gating ---------------------------
if [ -n "${EXPECT_COMMIT:-}" ]; then
  echo "[-] checking the device is on the commit being gated"
  device_commit=$(ssh -o BatchMode=yes "$DEVICE" 'git -C /data/openpilot rev-parse HEAD' 2>/dev/null || true)
  if [ "$device_commit" != "$EXPECT_COMMIT" ]; then
    cat >&2 <<BANNER

********************************************************************************
*  ❌  SMOKE GATE NOT RUN — the device is not on the release being gated.
*
*     device : ${device_commit:-<unreachable>}
*     release: $EXPECT_COMMIT
*
*  Gating now would test the PREVIOUS release and report a meaningless PASS.
*  Update the device first, then re-run the gate:
*
*    ssh $DEVICE 'sudo systemctl stop comma; cd /data/openpilot \\
*      && git fetch origin lean-release && git reset --hard FETCH_HEAD \\
*      && git submodule sync --recursive && git submodule update --init --recursive'
*    ssh $DEVICE sudo reboot            # first boot after a source-only
*                                       # release rebuilds natively (slow once)
*    EXPECT_COMMIT=$EXPECT_COMMIT ./tools/release/smoke_gate.sh
********************************************************************************
BANNER
    exit 1
  fi
  echo "[ok] device is on $EXPECT_COMMIT"
fi

# --- every shipped pkl must be loadable by the tinygrad this commit pins ------
# A pkl is only readable by the tinygrad revision that wrote it, and nothing
# else in the pipeline checks that: native_hash only guards release/prebuilt,
# and eagled's YOLO pkl is plain committed source that no manifest covers.
# Both have already shipped unreadable. Cheap to check, and a broken pkl makes
# the onroad smoke fail in a much more confusing way.
if [ "${SKIP_PKL_CHECK:-0}" = "1" ]; then
  echo "[gate] WARN: shipped-pkl check skipped by SKIP_PKL_CHECK=1" >&2
else
  echo "[-] checking shipped pkls load on the device"
  scp -q -o BatchMode=yes "$DIR/check_device_pkls.py" "$DEVICE:$REMOTE_PKL_CHECK"
  if ! ssh -o BatchMode=yes "$DEVICE" \
      "cd /tmp && DEV=QCOM PYTHONPATH=/data/openpilot \
       /usr/local/venv/bin/python $REMOTE_PKL_CHECK --root /data/openpilot"; then
    cat >&2 <<'BANNER'

********************************************************************************
*  ❌  SMOKE GATE FAILED — a shipped pkl cannot be read on the device.
*
*  The tinygrad revision this release pins does not match the one that compiled
*  the artifact. modeld and/or eagled will fail at startup. Rebuild the pkl
*  against the pinned tinygrad (scons for the driving model,
*  selfdrive/eagled/models/compile_yolo_onnx.py for YOLO) and re-release.
********************************************************************************
BANNER
    exit 1
  fi
fi

echo "[-] copying smoke harness to device"
scp -q -o BatchMode=yes "$DIR/smoke_onroad_device.py" "$DEVICE:$REMOTE_PATH"

echo "[-] running onroad smoke test (${DURATION}s); this stops any running openpilot"
# PYTHONPATH 必须含 /data/pydeps：sanic（lanlinkd 依赖）由 launch_chffrplus.sh
# 装 /data/pydeps 并注入 PYTHONPATH，smoke 环境要与真实启动环境一致，否则
# lanlinkd 在 smoke 里 import 崩溃（LanLinkEnabled 开启时）。
set +e
ssh -o BatchMode=yes "$DEVICE" \
  "cd /data/openpilot && sudo systemctl stop comma 2>/dev/null; \
   PYTHONPATH=/data/openpilot:/data/openpilot/openpilot:/data/pydeps \
   /usr/local/venv/bin/python $REMOTE_PATH $DURATION" 2>&1 | tee /tmp/smoke_gate.log
rc=${PIPESTATUS[0]}
set -e

# 冒烟停掉了 openpilot：无论门禁成败，收尾必须恢复运行（幂等）
ssh -o BatchMode=yes "$DEVICE" 'sudo systemctl start comma' 2>/dev/null || true

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
