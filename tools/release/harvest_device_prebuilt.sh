#!/usr/bin/env bash
# harvest_device_prebuilt.sh — 从设备回收原生构建的 camerad/loggerd/libparams（mac 上跑）。
#
# 容器构建不出/错这两份二进制：
#   - camerad：需要 QCOM 相机环境（arch=larch64 才编）
#   - loggerd：容器版链接容器 ffmpeg（libav*.so.61），AGNOS 上无对应库
#   - libparams_c.so：容器版未定义 __COMMA_HARDWARE__，会读 PC 参数目录而非 /data/params
# 设备端 build.py 首启时已原生构建好，回收进 fork（release/prebuilt/arm64/），
# 之后 build_lean_release_on_orb.sh 在 native 源码未变时随 release 发 prebuilt 标记，
# 设备开机跳过 build.py（省 ~9s）。
#
# 用法: ./tools/release/harvest_device_prebuilt.sh
# 环境变量: DEVICE (默认 comma@10.223.134.33)
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
ROOT="$(git -C "$DIR" rev-parse --show-toplevel)"
DEVICE="${DEVICE:-comma@10.223.134.33}"
DEST="$ROOT/release/prebuilt/arm64"
FILES="openpilot/system/camerad/camerad openpilot/system/loggerd/loggerd openpilot/common/libparams_c.so"

echo "[-] reading device state"
rel=$(ssh -o BatchMode=yes "$DEVICE" 'git -C /data/openpilot rev-parse HEAD')
# release commit 的 parent 即构建它的 lean-master commit（worktree detached commit）
src=$(git -C "$ROOT" rev-parse "$rel^" 2>/dev/null) || {
  echo "FATAL: release commit $rel unknown locally, run: git fetch fork lean-release"; exit 1; }
echo "     device release=$rel"
echo "     source (lean-master)=$src"

echo "[-] verifying + pulling device binaries"
for f in $FILES; do
  ssh -o BatchMode=yes "$DEVICE" "test -f /data/openpilot/$f" || {
    echo "FATAL: $f missing on device (device build.py 没跑过？)"; exit 1; }
  mkdir -p "$DEST/$(dirname "$f")"
  scp -q -o BatchMode=yes "$DEVICE:/data/openpilot/$f" "$DEST/$f"
done

hash=$("$ROOT/tools/release/prebuilt_native_hash.sh" "$src")
cat > "$DEST/MANIFEST" <<EOF
source_commit=$src
device_release=$rel
native_hash=$hash
harvested_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
files=$FILES
EOF

echo "[ok] harvested -> $DEST"
echo "     native_hash=$hash"
echo "     next: git add release/prebuilt && git commit && git push fork lean-master"
