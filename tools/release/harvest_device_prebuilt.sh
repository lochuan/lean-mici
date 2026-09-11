#!/usr/bin/env bash
# harvest_device_prebuilt.sh — pull device-built native artifacts from a comma
# device and stage them under release/prebuilt/arm64.
#
# The release must not ship container-built ELF files as runtime artifacts.
# This script therefore harvests the complete native artifact set from a device
# that has already built it with its own AGNOS toolchain.
#
# Usage:
#   ./tools/release/harvest_device_prebuilt.sh
#
# Environment:
#   DEVICE  comma device SSH target (default comma@10.223.134.33)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
DEVICE="${DEVICE:-comma@10.223.134.33}"
DEST="$ROOT/release/prebuilt/arm64"

echo "[-] reading device state"
rel=$(ssh -o BatchMode=yes "$DEVICE" 'git -C /data/openpilot rev-parse HEAD')
# A release commit is created on a detached worktree from the source branch,
# so its first parent is the lean-master commit that produced it.
src=$(git -C "$ROOT" rev-parse "$rel^" 2>/dev/null) || {
  echo "FATAL: release commit $rel unknown locally, run: git fetch fork lean-release" >&2
  exit 1
}
echo "     device release=$rel"
echo "     source commit=$src"

if ! ssh -o BatchMode=yes "$DEVICE" 'test -f /data/openpilot/prebuilt'; then
  echo "[warn] device has no prebuilt marker; artifacts may not have come from a complete prebuilt boot" >&2
fi

echo "[-] pulling device artifacts"
files=$(python3 "$DIR/release_lib.py" artifact-paths)
for f in $files; do
  if ! ssh -o BatchMode=yes "$DEVICE" "test -f /data/openpilot/$f"; then
    echo "FATAL: $f missing on device; device build.py 没跑过？" >&2
    exit 1
  fi
  mkdir -p "$DEST/$(dirname "$f")"
  scp -q -o BatchMode=yes "$DEVICE:/data/openpilot/$f" "$DEST/$f"
done

echo "[-] validating artifacts"
hash=$(./tools/release/prebuilt_native_hash.sh "$src")
python3 "$DIR/release_lib.py" write-manifest "$src" "$hash"
python3 "$DIR/release_lib.py" validate-artifacts

echo "[ok] harvested device prebuilts"
echo "     native_hash=$hash"
echo "     next:"
echo "       git add release/prebuilt"
echo "       git add -f release/prebuilt/arm64/openpilot/common/libparams_c.so"
echo "       git commit -m 'release: refresh device prebuilts'"
echo "       git push fork lean-master"
