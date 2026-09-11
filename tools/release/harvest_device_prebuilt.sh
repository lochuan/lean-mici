#!/usr/bin/env bash
# harvest_device_prebuilt.sh — pull device-built artifacts from a comma device
# and stage them under release/prebuilt/arm64.
#
# Two classes of artifact cannot be cross-built and must come from a device
# that has built them with its own AGNOS toolchain:
#   1. native ELFs — container glibc/ABI differs from AGNOS.
#   2. driving_tinygrad.pkl — holds tinygrad JIT kernels compiled for the
#      device's QCOM backend; a container build (DEV=CPU) is unusable.
#
# Prerequisite: the device must have built both, i.e. it booted without a
# `prebuilt` marker and ran build.py (full scons, no SKIP_TINYGRAD_COMPILE).
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
  # Provenance gate. A release overlay installs prebuilt binaries onto the
  # device, so "it is on the device" does NOT mean "the device built it". If a
  # cross-built ELF was shipped, the device keeps it (the `prebuilt` marker makes
  # build.py skip rebuilding) and harvesting would pull that same bad binary back
  # into the next release -- a self-perpetuating loop.
  #
  # A cross-built artifact has $HOME/.comma paths compiled in (see PC_PATH_MARKER
  # in release_lib.py). This shipped a PC-built pandad that read
  # /home/comma/.comma/params instead of /data/params, so the OBD multiplexing
  # handshake never completed and the car sat at "sunnypilot unavailable".
  if ssh -o BatchMode=yes "$DEVICE" "strings /data/openpilot/$f 2>/dev/null | grep -q '/\.comma'"; then
    echo "FATAL: $f on device is CROSS-BUILT (has \$HOME/.comma paths), not device-built." >&2
    echo "       Harvesting it would ship a broken binary. On the device run:" >&2
    echo "         rm -f /data/openpilot/prebuilt" >&2
    echo "         cd /data/openpilot && SKIP_TINYGRAD_COMPILE=1 scons -j4" >&2
    echo "       then re-run this script." >&2
    exit 1
  fi
  mkdir -p "$DEST/$(dirname "$f")"
  scp -q -o BatchMode=yes "$DEVICE:/data/openpilot/$f" "$DEST/$f"
done

# Non-ELF data artifacts (driving model pkl chunks). The pkl embeds tinygrad
# kernels compiled for the device's QCOM backend, so it cannot be cross-built;
# it must come from the device just like the native ELFs.
echo "[-] pulling device data artifacts"
# Globs can overlap (chunk* also matches chunkmanifest), so dedupe before scp;
# these are ~45MB each and we don't want to transfer any of them twice.
matches=""
for pattern in $(python3 "$DIR/release_lib.py" data-artifact-globs); do
  found=$(ssh -o BatchMode=yes "$DEVICE" "ls -1 /data/openpilot/$pattern 2>/dev/null" || true)
  matches="$matches$found
"
done
matches=$(printf '%s' "$matches" | sed '/^$/d' | sort -u)
data_found=0
for m in $matches; do
  rel="${m#/data/openpilot/}"
  mkdir -p "$DEST/$(dirname "$rel")"
  scp -q -o BatchMode=yes "$DEVICE:$m" "$DEST/$rel"
  data_found=$((data_found + 1))
done
if [ "$data_found" -eq 0 ]; then
  echo "FATAL: no driving model pkl found on device." >&2
  echo "       Build it there first (scons without SKIP_TINYGRAD_COMPILE), then re-run." >&2
  exit 1
fi
echo "     data artifacts: $data_found file(s)"

echo "[-] validating artifacts"
hash=$(./tools/release/prebuilt_native_hash.sh "$src")
python3 "$DIR/release_lib.py" write-manifest "$src" "$hash"
python3 "$DIR/release_lib.py" validate-artifacts

echo "[ok] harvested device prebuilts"
echo "     native_hash=$hash"
echo "     next:"
echo "       git add release/prebuilt"
echo "       git add -f release/prebuilt/arm64/openpilot/common/libparams_c.so"
echo "       # model pkl chunks are gitignored (*.pkl*), so force-add them too:"
echo "       git add -f release/prebuilt/arm64/openpilot/selfdrive/modeld/models/"
echo "       git commit -m 'release: refresh device prebuilts'"
echo "       git push fork lean-master"
