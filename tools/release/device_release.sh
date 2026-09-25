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
# 子模块目录（*_repo/panda）由 gitlink 物化步管理，内容漂移不卡发布
[ -z "$(git status --porcelain -- . ':!tinygrad_repo' ':!msgq_repo' ':!opendbc_repo' ':!rednose_repo' ':!panda' | grep -v '^??')" ] || { echo "工作树有未提交修改，拒绝发布" >&2; exit 1; }

echo "[-] 同步 lean-master 源内容到设备树（结构性防漂移：发布树 ≡ lean-master + 产物）"
# 设备到 GitHub 的 TLS 偶发握手失败（fake-IP 代理链路），重试 3 次
for i in 1 2 3; do
  git fetch origin lean-master:refs/remotes/origin/lean-master 2>/dev/null && break || sleep 5
done
git rev-parse -q --verify origin/lean-master >/dev/null || { echo "lean-master fetch 失败（3 次重试后）" >&2; exit 1; }
# 白名单 = 产物路径（ARTIFACT_PATHS + data globs 的实际文件）——设备树有、lean-master
# 没有的运行时产物是扁平模型的预期内容。
# ls 失败（glob 无匹配，如消费态树没有 yolo pkl）必须吞掉——否则 for 循环 rc≠0，
# 命令替换在 set -e 下静默杀死整个发布。
ART_EXPECT=$(
  /usr/local/venv/bin/python /tmp/relhelper/release_lib.py artifact-paths
  /usr/local/venv/bin/python /tmp/relhelper/release_lib.py flat-tree-entries
  for pat in $(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py data-artifact-globs); do ls "$pat" 2>/dev/null || true; done
)
git checkout origin/lean-master -- .
# 镜像 lean-master 的删除：源里删掉的文件（功能移除）从设备树一并移除，
# 否则发布树继续携带死代码。白名单（构建产物）不删——产物在 HEAD 里 tracked、
# lean-master 没有，属于扁平模型的预期内容。
# grep -v 在"零行需要删除"时退出 1（本版本与上一版无删除差异时必然发生），
# pipefail+set -e 会无声杀死整个发布。与 ls glob 空匹配同类坑，|| true 兜底。
comm -23 \
  <(git ls-files | sort) \
  <(git ls-tree -r --name-only origin/lean-master | sort) \
  | { grep -vxF -f <(echo "$ART_EXPECT" | sort -u) || true; } \
  | xargs -r rm -f
AM_BAD=$(git diff --name-only --diff-filter=AM origin/lean-master | grep -vxF -f <(echo "$ART_EXPECT" | sort -u) || true)
if [ -n "$AM_BAD" ]; then
  echo "$AM_BAD" | head -20 >&2
  echo "设备树同步 lean-master 后仍有非产物的新增/修改，拒绝发布（排查 .gitignore/权限）" >&2
  exit 1
fi
echo "[ok] 设备树源内容 ≡ origin/lean-master（+ 运行时产物）"
echo "[ok] 设备树 ≡ origin/lean-master（源内容）"

echo "[-] Materialize tinygrad at the lean-master gitlink T=$SECONDS"
TG_SHA=$(git rev-parse origin/lean-master:tinygrad_repo)
CUR_PIN="$(cat "$SRC/tinygrad_repo/TINYGRAD_PIN" 2>/dev/null || true)"
if [ "$CUR_PIN" = "$TG_SHA" ]; then
  echo "[ok] tinygrad 已在 $TG_SHA"
else
  rm -rf /data/tg_materialize && git init -q /data/tg_materialize
  git -C /data/tg_materialize remote add origin https://github.com/tinygrad/tinygrad.git
  for i in 1 2 3; do git -C /data/tg_materialize fetch -q --depth=1 origin "$TG_SHA" && break || sleep 5; done
  git -C /data/tg_materialize checkout -q FETCH_HEAD
  rm -rf "$SRC/tinygrad_repo" && cp -a /data/tg_materialize "$SRC/tinygrad_repo" && rm -rf "$SRC/tinygrad_repo/.git"
  echo "$TG_SHA" > "$SRC/tinygrad_repo/TINYGRAD_PIN"
  echo "[ok] tinygrad materialized at $TG_SHA"
fi

echo "[-] Materialize 构建 gitlink（msgq/rednose/panda；扁平树只带运行时子集）T=$SECONDS"
# SConstruct 的 toolpath 需要 msgq_repo/rednose_repo 的 site_scons site_tools，
# panda/SConscript 需要 panda 的构建源，opendbc 的运行时/车辆接口代码也必须
# 跟 gitlink pin 走（SecOC 降噪修复即一例）—— 这些都是 gitlink 内容，扁平树不带。
# pin 放 /data/matpins（git 树外）：checkout/reset 会删 repo 内被 track 的文件
# 却可能留下 repo 内 pin —— 2026-09-25 实录：pin 在内容缺一半时照样判"已在"，
# scons 死在 No tool module 'cython'。pin 一致还必须验 canary 文件在位。
# canary = 该 repo 的构建关键文件。发布 commit 不再带 pin。
PIN_DIR=/data/matpins
materialize_repo() {
  local name="$1" url="$2" canary="$3"
  local sha pin cur
  sha=$(git -C "$SRC" rev-parse "origin/lean-master:$name")
  pin="$PIN_DIR/${name}.sha"
  cur="$(cat "$pin" 2>/dev/null || true)"
  rm -f "$SRC/$name/.materialized_sha"   # 旧版 repo 内 pin 退役
  if [ "$cur" = "$sha" ] && [ -e "$SRC/$name/$canary" ]; then
    echo "[ok] $name 已在 $sha"
    return
  fi
  local tmp="/tmp/mat_${name%_repo}"
  rm -rf "$tmp" && git init -q "$tmp"
  git -C "$tmp" remote add origin "$url"
  for i in 1 2 3; do git -C "$tmp" fetch -q --depth=1 origin "$sha" && break || sleep 5; done
  git -C "$tmp" checkout -q FETCH_HEAD
  rm -rf "$SRC/$name" && cp -a "$tmp" "$SRC/$name" && rm -rf "$SRC/$name/.git"
  mkdir -p "$PIN_DIR"
  echo "$sha" > "$pin"
  echo "[ok] $name materialized at $sha"
}
materialize_repo msgq_repo https://github.com/commaai/msgq.git site_scons/site_tools/cython.py
materialize_repo rednose_repo https://github.com/commaai/rednose.git site_scons/site_tools/rednose_filter.py
materialize_repo panda https://github.com/commaai/panda.git SConscript
materialize_repo opendbc_repo https://github.com/lochuan/opendbc.git opendbc/car/car.capnp

echo "[-] 全量重建 native 产物（ARTIFACT_PATHS）T=$SECONDS"
# 扁平树发布不带 scons 步骤的历史欠账：launch_chffrplus.sh 的运行时 prebuilt
# 标记让 build.py 永远跳过，C++ 源码改动（如 params_keys.h）在旧流程下根本
# 不会进二进制 —— 2026-09-24 的 libparams_c.so 键表滞后就是这一类。
# checkout -- . 恢复了 SConstruct/SConscript；SKIP_CAPNP_REGEN=1：lean AGNOS
# 没有 capnpc 工具链，gen/cpp 已随 lean-master 跟踪（2026-09-24 起）。
# 显式列出 ARTIFACT_PATHS 目标：默认全量会把 eagled 的 yolo pkl 也拉进图里，
# 而它的 onnx 源在扁平树被剥离，且 pkl 由本脚本自己的步骤重编。
# 若某次改动动了 .capnp schema，必须先在 Mac 侧重新生成 gen/cpp 再提交，
# 否则这里的编译用的是旧生成物 —— 目前没有工具能拦这个类别。
for n in 4 5 6 7; do
  [ "$(cat /sys/devices/system/cpu/cpu$n/online 2>/dev/null)" = "0" ] && echo 1 | sudo tee /sys/devices/system/cpu/cpu$n/online >/dev/null
done
ART_TARGETS=$(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py artifact-paths)
# 先删后建：设备 HEAD track 的旧产物被任何 reset/checkout 恢复后，mtime 比新编的
# .o 还新，scons 会误判"已是最新"跳过重链（2026-09-24 键表门禁首拦实录）。
# 对象文件不在此列（未被 git track），删除目标只触发链接，秒级。
while IFS= read -r t; do rm -f "$SRC/$t"; done <<< "$ART_TARGETS"
(
  cd "$SRC"
  export PATH="/usr/local/venv/bin:$PATH"
  SKIP_CAPNP_REGEN=1 PYTHONPATH="$SRC:$SRC/openpilot" \
    /usr/local/venv/bin/scons -j4 $ART_TARGETS
) || { echo "native 全量重建失败，拒绝发布" >&2; exit 1; }
echo "[ok] native 全量重建完成 T=$SECONDS"

echo "[-] eagled YOLO pkl（输入指纹未变则跳过重编）T=$SECONDS"
# pkl 嵌入的是某一个 tinygrad revision 的 JIT kernel：pin/onnx/编译脚本/编译
# 参数任一变化才需要重编（2026-09-25 议定），其余情况重编纯浪费 ~5 分钟。
YOLO_DIR="openpilot/selfdrive/eagled/models"
YOLO_PKL="$SRC/$YOLO_DIR/yolo_tinygrad.pkl"
YOLO_ONNX="$SRC/$YOLO_DIR/yolo26n-bdd7-fp32-384x640.onnx"
[ -f "$YOLO_ONNX" ] || { echo "yolo onnx 缺失：$YOLO_ONNX —— lean-master 应 tracked 此文件，前置同步步应已落盘" >&2; exit 1; }
YOLO_FP=$(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py fingerprint \
  --extra "yolo-pkl-v1" --extra "pin=$TG_SHA" \
  "$YOLO_ONNX" "$SRC/$YOLO_DIR/compile_yolo_onnx.py")
if [ -f "$YOLO_PKL" ] && [ "$(cat "$YOLO_PKL.inputs_fp" 2>/dev/null)" = "$YOLO_FP" ]; then
  echo "[ok] yolo pkl 输入未变，跳过重编 T=$SECONDS"
else
  for n in 4 5 6 7; do
    [ "$(cat /sys/devices/system/cpu/cpu$n/online 2>/dev/null)" = "0" ] && echo 1 | sudo tee /sys/devices/system/cpu/cpu$n/online >/dev/null
  done
  (
    cd "$SRC"
    DEV=QCOM:IR3 IMAGE=1 FLOAT16=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 PARALLEL=0 \
    PYTHONPATH="$SRC/tinygrad_repo:$SRC" \
    /usr/local/venv/bin/python "$SRC/$YOLO_DIR/compile_yolo_onnx.py" "$YOLO_ONNX" "$YOLO_PKL"
  ) || { echo "yolo pkl 编译失败，拒绝发布" >&2; exit 1; }
  # 不按 driving 的 get_chunk_targets 切块：yolo pkl ~13MB 远低于按 onnx 估算的
  # 切块上限（2*onnx+10MB ≈ 29MB），且运行时 TinygradRunner 按单文件直读，
  # 切块反而会破坏加载。模型长大越过上限时需连同运行时加载器一起改造。
  echo "$YOLO_FP" > "$YOLO_PKL.inputs_fp"
  echo "[ok] yolo pkl 重编译完成 T=$SECONDS"
fi

echo "[-] 内置 driving 模型（输入指纹未变则跳过重编）T=$SECONDS"
MODEL_DIR="$SRC/openpilot/selfdrive/modeld"
DRIVE_PKL="$MODEL_DIR/models/driving_tinygrad.pkl"
DRIVE_ONNX="$MODEL_DIR/models/driving_supercombo.onnx"
[ -f "$DRIVE_ONNX" ] || { echo "driving onnx 缺失：$DRIVE_ONNX —— lean-master 应 tracked 此文件，前置同步步应已落盘" >&2; exit 1; }
DRIVE_ARGS="--model-size 512x256 --camera-resolutions 1344x760 --frame-skip 4"
DRIVE_FP=$(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py fingerprint \
  --extra "driving-pkl-v1" --extra "pin=$TG_SHA" --extra "$DRIVE_ARGS" \
  "$DRIVE_ONNX" "$MODEL_DIR/compile_modeld.py" "$MODEL_DIR/get_model_metadata.py" "$MODEL_DIR/helpers.py")
if [ -f "$DRIVE_PKL" ] && [ -f "$DRIVE_PKL.chunkmanifest" ] && [ "$(cat "$DRIVE_PKL.inputs_fp" 2>/dev/null)" = "$DRIVE_FP" ]; then
  echo "[ok] driving pkl 输入未变，跳过重编 T=$SECONDS"
else
  for n in 4 5 6 7; do
    [ "$(cat /sys/devices/system/cpu/cpu$n/online 2>/dev/null)" = "0" ] && echo 1 | sudo tee /sys/devices/system/cpu/cpu$n/online >/dev/null
  done
  (
    cd "$SRC"
    DEV=QCOM IMAGE=1 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 PARALLEL=0 \
    PYTHONPATH="$SRC/tinygrad_repo:$SRC" \
    taskset -c 4 /usr/local/venv/bin/python "$MODEL_DIR/compile_modeld.py" \
      --model-size 512x256 \
      --camera-resolutions 1344x760 \
      --onnx "$DRIVE_ONNX" \
      --output "$DRIVE_PKL" \
      --frame-skip 4
  ) || { echo "内置 driving 模型编译失败，拒绝发布" >&2; exit 1; }
  # 按 SConscript 的估算切块（pkl 超过单文件上限）
  (
    cd "$SRC"
    PYTHONPATH="$SRC/openpilot" /usr/local/venv/bin/python - "$DRIVE_PKL" <<'PYEOF'
import os, sys
from openpilot.common.file_chunker import chunk_file, get_chunk_targets
pkl = sys.argv[1]
onnx = pkl.replace("driving_tinygrad.pkl", "driving_supercombo.onnx")
targets = get_chunk_targets(pkl, 2.0 * os.path.getsize(onnx) + 10 * 1024 * 1024)
chunk_file(pkl, targets)
print("chunked:", [os.path.basename(t) for t in targets])
PYEOF
  ) || { echo "driving pkl 切块失败，拒绝发布" >&2; exit 1; }
  echo "$DRIVE_FP" > "$DRIVE_PKL.inputs_fp"
  echo "[ok] 内置 driving 模型重编译完成 T=$SECONDS"
fi

echo "[-] capnp schema/gen 一致性门禁 T=$SECONDS"
# 设备无 capnpc,SKIP_CAPNP_REGEN=1 编译 checked-in gen/cpp —— schema 改了忘记
# Mac 侧重生成会静默编译旧结构(params 键表事故的同类缺口,2026-09-25 议定)。
# schema 清单走唯一登记表（openpilot/cereal/schemas.py），此处不再手抄路径。
/usr/local/venv/bin/python /tmp/relhelper/release_lib.py check-schema-stamp \
  --root "$SRC" "$SRC/openpilot/cereal/gen/cpp"

echo "[-] params 键表门禁：params_keys.h 的每个键必须已编译进 libparams_c.so T=$SECONDS"
/usr/local/venv/bin/python /tmp/relhelper/release_lib.py check-params-keys "$SRC"

echo "[-] 等 GPU 从编译负载回落（EGL 需要干净的显示/DRM 状态）T=$SECONDS"
sleep 15

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
# .overlay_init 是 updater 的运行时 overlay 标记（updated.py 管理、随更新周期
# 创建/删除）——track 进 release commit 会让运行时删除变成"树脏"，每次重启后
# 的发布都卡在前提检查。结构白名单仍保留该条目（防镜像删除误伤），但 git 不跟踪。
git add -f . ':(exclude).overlay_init'
git -c core.compression=0 -c gc.auto=0 commit -m "openpilot v$VERSION lean release (device-built, flat)

date: $DATETIME
master commit: $MASTER_SHA
built on: comma device (smoke-verified before publish)"

echo "[ok] 扁平树 stage 就绪: $STAGE（分支 lean-release, 单 commit），等 Mac 侧取走推送"
echo "    树大小: $(du -sh "$STAGE" | cut -f1)"
