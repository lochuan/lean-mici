#!/usr/bin/env bash
#
# device_release.sh — 在 comma 设备上发布 lean-release（扁平树，上游 sunnypilot 模型）。
#
# 发布树 = 本设备的构建树剥离后的运行时快照：
#   * 产物在运行时路径（消费设备 git reset 即用，无需 overlay/编译）
#   * 完整性由构建过程结构性保证（剥离只去掉构建输入与中间产物，不靠人工清单）
#   * `prebuilt` 标记随树发布：由发布机的"冒烟验证过的运行中构建"挣得
#   * 单孤儿 commit（每次发布全新 git init，上游 publish.sh 模式）
#   * 发布前 60s 冒烟：PASS 才准发布；EXIT trap 保证任何失败都恢复 comma 运行
#
# 用法（设备上）:  bash tools/release/device_release.sh
# 产出: /data/relstage（扁平树 git 仓库，分支 lean-release），由 Mac 侧取走推送。
set -euo pipefail

STAGE=/data/relstage
SRC=/data/openpilot

cleanup() {
  # 冒烟停掉了 openpilot：无论成败，收尾必须恢复运行（幂等）
  sudo systemctl start comma
}
trap cleanup EXIT

echo "[-] 前提检查"
sudo systemctl is-active --quiet comma || { echo "comma 未运行（产物必须来自正在运行的构建）" >&2; exit 1; }
[ -z "$(git status --porcelain | grep -v '^??')" ] || { echo "工作树有未提交修改，拒绝发布" >&2; exit 1; }

echo "[-] 同步 lean-master 源内容到设备树（结构性防漂移：发布树 ≡ lean-master + 产物）"
git fetch origin lean-master:refs/remotes/origin/lean-master
git checkout origin/lean-master -- .
# 同步后仍有新增/修改 = lean-master 的源内容没同步干净，拒绝发布；
# 白名单：运行时产物（构建输出，lean-master 不跟踪）是扁平模型的预期内容，放行。
# 校验基准 = 产物路径（ARTIFACT_PATHS + data globs 的实际文件），相对 origin/lean-master 的
# 「删除」同样放行（产物在 HEAD 里 tracked、lean-master 没有）。
# ls 失败（glob 无匹配，如消费态树没有 yolo pkl）必须吞掉——否则 for 循环 rc≠0，
# 命令替换在 set -e 下静默杀死整个发布。
ART_EXPECT=$(
  python3 tools/release/release_lib.py artifact-paths
  python3 tools/release/release_lib.py flat-tree-entries
  for pat in $(python3 tools/release/release_lib.py data-artifact-globs); do ls "$pat" 2>/dev/null || true; done
)
AM_BAD=$(git diff --name-only --diff-filter=AM origin/lean-master | grep -vxF -f <(echo "$ART_EXPECT" | sort -u) || true)
if [ -n "$AM_BAD" ]; then
  echo "$AM_BAD" | head -20 >&2
  echo "设备树同步 lean-master 后仍有非产物的新增/修改，拒绝发布（排查 .gitignore/权限）" >&2
  exit 1
fi
echo "[ok] 设备树源内容 ≡ origin/lean-master（+ 运行时产物）"
echo "[ok] 设备树 ≡ origin/lean-master（源内容）"

echo "[-] 编译 avoidanced YOLO pkl（prebuilt 模式下 scons 不跑，SConscript 规则在此补偿）T=$SECONDS"
YOLO_DIR="openpilot/selfdrive/avoidanced/models"
YOLO_PKL="$SRC/$YOLO_DIR/yolo_tinygrad.pkl"
YOLO_ONNX="$SRC/$YOLO_DIR/yolo26n-bdd7-fp32-384x640.onnx"
DEVICE_PIN="$(cat "$SRC/tinygrad_repo/TINYGRAD_PIN" 2>/dev/null || true)"
PKL_PIN="$(cat "$YOLO_PKL.tinygrad_pin" 2>/dev/null || true)"
if [ -f "$YOLO_PKL" ] && [ -n "$DEVICE_PIN" ] && [ "$PKL_PIN" = "$DEVICE_PIN" ]; then
  echo "[ok] yolo pkl 已匹配本树 pin（$DEVICE_PIN），跳过重编译"
else
  for n in 4 5 6 7; do
    [ "$(cat /sys/devices/system/cpu/cpu$n/online 2>/dev/null)" = "0" ] && echo 1 | sudo tee /sys/devices/system/cpu/cpu$n/online >/dev/null
  done
  (
    cd "$SRC"
    DEV=QCOM:IR3 IMAGE=1 FLOAT16=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 PARALLEL=0 \
    PYTHONPATH="$SRC/tinygrad_repo" \
    /usr/local/venv/bin/python "$SRC/$YOLO_DIR/compile_yolo_onnx.py" "$YOLO_ONNX" "$YOLO_PKL"
  ) || { echo "yolo pkl 编译失败，拒绝发布" >&2; exit 1; }
  echo "[ok] yolo pkl 编译完成 T=$SECONDS"
fi

echo "[-] 60s 冒烟（确定设备正常运行）T=$SECONDS"
sudo systemctl stop comma
PYTHONPATH=/data/openpilot:/data/openpilot/openpilot \
  /usr/local/venv/bin/python /tmp/relhelper/smoke_onroad_device.py 60
echo "[ok] smoke PASS"

echo "[-] 组装扁平树 stage"
sudo rm -rf "$STAGE"
mkdir -p "$STAGE"
rsync -a "$SRC/" "$STAGE/" \
  --exclude=.git \
  --exclude=.sconsign.dblite \
  --exclude=.github \
  --exclude=.claude \
  --exclude=__pycache__ \
  --exclude='*.pyc' \
  --exclude='*.o' \
  --exclude='*.a' \
  --exclude='*.os' \
  --exclude=SConstruct \
  --exclude=SConscript \
  --exclude=site_scons \
  --exclude='tools/release' \
  --exclude='release/' \
  --exclude='*.onnx' \
  --exclude='*.onnx.data' \
  --exclude=node_modules \
  --exclude='openpilot/selfdrive/modeld/models/*.onnx*'

cd "$STAGE"

# rsync exclude 覆盖不了的收尾剥离
find . -name '.git' -exec rm -rf {} + 2>/dev/null || true        # submodule 指针文件
find . -name '*.onnx*' -delete 2>/dev/null || true
rm -rf tinygrad_repo/examples tinygrad_repo/tests tinygrad_repo/docs \
       tinygrad_repo/scripts tinygrad_repo/.github
rm -rf .sconsign.dblite
find openpilot/third_party \( -iname '*x86*' -o -iname '*darwin*' \) -exec rm -rf {} + 2>/dev/null || true

# PC 路径守卫全量扫描：扁平树里所有 ELF 都不得含 /.comma 标记
# （09-11 交叉构建事故的防线；设备本树构建的结构性产物，此处为回归防线）
echo "[-] stamp tinygrad pin（发布树剥了 .git，这是选择器门控唯一可读的树 pin）"
# pin 取自 lean-master 的 gitlink（ls-tree）——设备树可能是扁平消费者，
# tinygrad_repo 无 .git，rev-parse 会穿透父仓库返回 release commit（垃圾 pin）
python3 - "$SRC" "$STAGE" "refs/remotes/origin/lean-master" <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, "/tmp/relhelper")
import release_lib

sha = release_lib.stamp_tinygrad_pin(Path(sys.argv[2]), Path(sys.argv[1]), treeish=sys.argv[3])
print(f"[ok] tinygrad pin stamped: {sha[:7]}")
PYEOF

python3 - <<'PYEOF'
import os, sys
from pathlib import Path
sys.path.insert(0, "/tmp/relhelper")
import release_lib

bad = []
for root, _, files in os.walk("."):
    for fn in files:
        p = Path(os.path.join(root, fn))
        try:
            with p.open("rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        if release_lib.has_pc_paths(p):
            bad.append(p)
if bad:
    print("PC-built ELF in release tree:", *bad[:10], sep="\n  ", file=sys.stderr)
    sys.exit(1)
print(f"PC-path guard: all ELFs clean")

# 发布门禁：stage 树必须能自行解析 pin，否则选择器四层门控全失效（无 .git 扁平树）
pin = release_lib.stage_tinygrad_pin(Path("."))
if pin is None:
    print("stage tree cannot resolve its tinygrad pin; refusing to publish", file=sys.stderr)
    sys.exit(1)
print(f"release gate: tinygrad pin {pin[:7]} resolvable in stage tree")
PYEOF

echo "[-] touch prebuilt（发布机构建已通过冒烟验证）"
touch prebuilt

VERSION=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' openpilot/sunnypilot/common/version.h | head -1)
echo "[-] 组发布 commit: openpilot v$VERSION lean release (device-built, flat)"
DATETIME=$(date '+%Y-%m-%dT%H:%M:%S')
MASTER_SHA=$(git -C "$SRC" rev-parse origin/lean-master)
git init -q -b lean-release
git config user.name lochuan
git config user.email lochuan@users.noreply.github.com
git add -f .
git -c core.compression=0 -c gc.auto=0 commit -m "openpilot v$VERSION lean release (device-built, flat)

date: $DATETIME
master commit: $MASTER_SHA
built on: comma device (smoke-verified before publish)"

echo "[ok] 扁平树 stage 就绪: $STAGE（分支 lean-release, 单 commit），等 Mac 侧取走推送"
echo "    树大小: $(du -sh "$STAGE" | cut -f1)"
