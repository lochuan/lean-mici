#!/usr/bin/env bash
#
# device_release.sh — 在 comma 设备上发布扁平树 release（上游 sunnypilot 模型）。
#
# 发布树 = 本设备的构建树剥离后的运行时快照：
#   * 产物在运行时路径（消费设备 git reset 即用，无需 overlay/编译）
#   * 完整性由构建过程结构性保证（剥离只去掉构建输入与中间产物，不靠人工清单）
#   * `prebuilt` 标记随树发布（发布机构建，无发布前冒烟）
#   * 单孤儿 commit（每次发布全新 git init，上游 publish.sh 模式）
#   * EXIT trap 保证任何失败都恢复 comma 运行
#
# 用法（设备上）:  bash tools/release/device_release.sh
# 产出: /data/relstage（扁平树 git 仓库，分支 $RELEASE_BRANCH），由 Mac 侧取走推送。
#
# 分支参数（环境变量，缺省 = 主线发版）:
#   SRC_BRANCH     源分支（源内容与 gitlink pin 的权威），缺省 lean-master
#   RELEASE_BRANCH 发布分支（relstage 单孤儿 commit 的分支名），缺省 lean-release
# 例: SRC_BRANCH=big-uplink RELEASE_BRANCH=big-release bash tools/release/device_release.sh
#
# 壳样板只定义一次（fix/release-stages ①）：阶段开头 run_stage、已知失败话术
# die、CPU 唤醒 wake_cpu_cores、fetch 重试 git_fetch_retry。新阶段请走这些
# 帮助函数，不要另抄一份开头与失败包装。
set -Eeuo pipefail   # -E：ERR trap 继承进阶段函数，统一失败话术才落得到位

STAGE=/data/relstage
SRC=/data/openpilot
SRC_BRANCH="${SRC_BRANCH:-lean-master}"
RELEASE_BRANCH="${RELEASE_BRANCH:-lean-release}"
SRC_REF="origin/$SRC_BRANCH"

CURRENT_STAGE=""

die() {
  echo "$*" >&2
  exit 1
}

cleanup() {
  # 收尾必须保证 comma 在运行（幂等）
  sudo systemctl start comma
}
trap cleanup EXIT

on_err() {
  # 统一失败话术 + 非零拒发：非预期失败一律报「标题 失败，拒绝发布」。
  # 命令替换/子 shell 内的失败只静默向上传播退出码——话术在最外层报一次，
  # 否则 [ -z "$(… | grep …)" ] 这类正常空输出路径会刷假报错。
  if [ "${BASH_SUBSHELL:-0}" -gt 0 ]; then
    exit 1
  fi
  die "${CURRENT_STAGE:-发布管线} 失败，拒绝发布"
}
trap on_err ERR

run_stage() {
  # run_stage "标题" 命令 [参数...] —— 统一 T=$SECONDS 标题、失败话术、非零拒发
  CURRENT_STAGE="$1"
  echo "[-] $1 T=$SECONDS"
  shift
  "$@"
  CURRENT_STAGE=""
}

wake_cpu_cores() {
  # 3 个构建阶段共用：把 4~7 号核上线再吃编译负载
  local n
  for n in 4 5 6 7; do
    if [ "$(cat /sys/devices/system/cpu/cpu$n/online 2>/dev/null)" = "0" ]; then
      echo 1 | sudo tee /sys/devices/system/cpu/cpu$n/online >/dev/null
    fi
  done
}

git_fetch_retry() {
  # git_fetch_retry <git 参数...> —— 设备到 GitHub 的 TLS 偶发握手失败
  # （fake-IP 代理链路），重试 3 次；三次都失败返回非零。
  local i
  for i in 1 2 3; do
    git "$@" && return 0
    sleep 5
  done
  return 1
}

pkl_cache_hit() {
  # pkl_cache_hit <pkl> <输入指纹> —— 指纹未变则跳过重编（2026-09-25 议定）。
  # 嵌入 tinygrad JIT kernel 的 pkl 只随 pin/onnx/编译脚本/编译参数变。
  # 产物认整文件或切块 manifest：driving pkl 编完即切块、原文件删掉
  { [ -f "$1" ] || [ -f "$1.chunkmanifest" ]; } && [ "$(cat "$1.inputs_fp" 2>/dev/null)" = "$2" ]
}

check_prereqs() {
  sudo systemctl is-active --quiet comma || die "comma 未运行（产物必须来自正在运行的构建）"
  # 子模块目录（*_repo/panda）由 gitlink 物化步管理，内容漂移不卡发布
  [ -z "$(git status --porcelain -- . ':!tinygrad_repo' ':!msgq_repo' ':!opendbc_repo' ':!rednose_repo' ':!panda' | grep -v '^??')" ] || die "工作树有未提交修改，拒绝发布"
}

sync_sources() {
  cd "$SRC"
  # 设备到 GitHub 的 TLS 偶发握手失败（fake-IP 代理链路），重试 3 次。
  # 重试尽失败不直接拒：ref 验证行才是门（ref 可能是上轮 fetch 留下的）。
  git_fetch_retry fetch origin "$SRC_BRANCH:refs/remotes/origin/$SRC_BRANCH" 2>/dev/null || true
  git rev-parse -q --verify "$SRC_REF" >/dev/null || die "$SRC_BRANCH fetch 失败（3 次重试后）"
  # 运行时产物 + flat-tree 结构条目是源树之外的预期内容。
  ART_EXPECT=$(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py source-sync-allowlist)
  git checkout "$SRC_REF" -- .
  SYNC_PLAN=$(printf '%s\n' "$ART_EXPECT" | /usr/local/venv/bin/python /tmp/relhelper/release_lib.py plan-source-sync "$SRC_REF")
  SYNC_REJECT=""
  while IFS=$'\t' read -r kind path; do
    case "$kind" in
      D) rm -f -- "$path" ;;
      R) SYNC_REJECT="${SYNC_REJECT}${path}"$'\n' ;;
    esac
  done <<< "$SYNC_PLAN"
  if [ -n "$SYNC_REJECT" ]; then
    printf '%s' "$SYNC_REJECT" | head -20 >&2
    die "设备树同步 $SRC_BRANCH 后仍有非产物的新增/修改，拒绝发布（排查 .gitignore/权限）"
  fi
  echo "[ok] 设备树源内容 ≡ $SRC_REF（+ 运行时产物）"
  echo "[ok] 设备树 ≡ $SRC_REF（源内容）"
}

materialize_tinygrad() {
  TG_SHA=$(git rev-parse "$SRC_REF:tinygrad_repo")
  CUR_PIN="$(cat "$SRC/tinygrad_repo/TINYGRAD_PIN" 2>/dev/null || true)"
  if [ "$CUR_PIN" = "$TG_SHA" ]; then
    echo "[ok] tinygrad 已在 $TG_SHA"
    return
  fi
  rm -rf /data/tg_materialize && git init -q /data/tg_materialize
  git -C /data/tg_materialize remote add origin https://github.com/tinygrad/tinygrad.git
  git_fetch_retry -C /data/tg_materialize fetch -q --depth=1 origin "$TG_SHA" || die "tinygrad fetch 失败（3 次重试后）"
  git -C /data/tg_materialize checkout -q FETCH_HEAD
  rm -rf "$SRC/tinygrad_repo" && cp -a /data/tg_materialize "$SRC/tinygrad_repo" && rm -rf "$SRC/tinygrad_repo/.git"
  echo "$TG_SHA" > "$SRC/tinygrad_repo/TINYGRAD_PIN"
  echo "[ok] tinygrad materialized at $TG_SHA"
}

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
  sha=$(git -C "$SRC" rev-parse "$SRC_REF:$name")
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
  git_fetch_retry -C "$tmp" fetch -q --depth=1 origin "$sha" || die "$name fetch 失败（3 次重试后）"
  git -C "$tmp" checkout -q FETCH_HEAD
  rm -rf "$SRC/$name" && cp -a "$tmp" "$SRC/$name" && rm -rf "$SRC/$name/.git"
  mkdir -p "$PIN_DIR"
  echo "$sha" > "$pin"
  echo "[ok] $name materialized at $sha"
}

materialize_gitlinks() {
  materialize_repo msgq_repo https://github.com/commaai/msgq.git site_scons/site_tools/cython.py
  materialize_repo rednose_repo https://github.com/commaai/rednose.git site_scons/site_tools/rednose_filter.py
  materialize_repo panda https://github.com/commaai/panda.git SConscript
  materialize_repo opendbc_repo https://github.com/lochuan/opendbc.git opendbc/car/car.capnp
}

rebuild_native() {
  # 扁平树发布不带 scons 步骤的历史欠账：launch_chffrplus.sh 的运行时 prebuilt
  # 标记让 build.py 永远跳过，C++ 源码改动（如 params_keys.h）在旧流程下根本
  # 不会进二进制 —— 2026-09-24 的 libparams_c.so 键表滞后就是这一类。
  # checkout -- . 恢复了 SConstruct/SConscript；SKIP_CAPNP_REGEN=1：lean AGNOS
  # 没有 capnpc 工具链，gen/cpp 已随 lean-master 跟踪（2026-09-24 起）。
  # 显式列出 ARTIFACT_PATHS 目标：默认全量会把需在设备上编译的
  # pkl 也拉进图里，而它们由本脚本自己的步骤重编。
  # 若某次改动动了 .capnp schema，必须先在 Mac 侧重新生成 gen/cpp 再提交，
  # 否则这里的编译用的是旧生成物 —— 目前没有工具能拦这个类别。
  wake_cpu_cores
  ART_TARGETS=$(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py artifact-paths)
  # 先删后建：设备 HEAD track 的旧产物被任何 reset/checkout 恢复后，mtime 比新编的
  # .o 还新，scons 会误判"已是最新"跳过重链（2026-09-24 键表门禁首拦实录）。
  # 对象文件不在此列（未被 git track），删除目标只触发链接，秒级。
  while IFS= read -r t; do rm -f "$SRC/$t"; done <<< "$ART_TARGETS"
  (
    cd "$SRC"
    export PATH="/usr/local/venv/bin:$PATH"
    # -j8：2026-10-03 供电确认没问题，6→8 吃满 8 核
    SKIP_CAPNP_REGEN=1 PYTHONPATH="$SRC:$SRC/openpilot" \
      /usr/local/venv/bin/scons -j8 $ART_TARGETS
  ) || die "native 全量重建失败，拒绝发布"
  echo "[ok] native 全量重建完成 T=$SECONDS"
}

compile_driving_pkl() {
  MODEL_DIR="$SRC/openpilot/selfdrive/modeld"
  DRIVE_PKL="$MODEL_DIR/models/driving_tinygrad.pkl"
  DRIVE_ONNX="$MODEL_DIR/models/driving_supercombo.onnx"
  [ -f "$DRIVE_ONNX" ] || die "driving onnx 缺失：$DRIVE_ONNX —— $SRC_BRANCH 应 tracked 此文件，前置同步步应已落盘"
  DRIVE_ARGS="--model-size 512x256 --camera-resolutions 1344x760 --frame-skip 4"
  DRIVE_FP=$(/usr/local/venv/bin/python /tmp/relhelper/release_lib.py fingerprint \
    --extra "driving-pkl-v1" --extra "pin=$TG_SHA" --extra "$DRIVE_ARGS" \
    "$DRIVE_ONNX" "$MODEL_DIR/compile_modeld.py" "$MODEL_DIR/get_model_metadata.py" "$MODEL_DIR/helpers.py")
  if pkl_cache_hit "$DRIVE_PKL" "$DRIVE_FP" && [ -f "$DRIVE_PKL.chunkmanifest" ]; then
    echo "[ok] driving pkl 输入未变，跳过重编 T=$SECONDS"
    return
  fi
  wake_cpu_cores
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
  ) || die "内置 driving 模型编译失败，拒绝发布"
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
  ) || die "driving pkl 切块失败，拒绝发布"
  echo "$DRIVE_FP" > "$DRIVE_PKL.inputs_fp"
  echo "[ok] 内置 driving 模型重编译完成 T=$SECONDS"
}

check_schema_stamp() {
  # 设备无 capnpc,SKIP_CAPNP_REGEN=1 编译 checked-in gen/cpp —— schema 改了忘记
  # Mac 侧重生成会静默编译旧结构(params 键表事故的同类缺口,2026-09-25 议定)。
  # schema 清单走唯一登记表（openpilot/cereal/schemas.py），此处不再手抄路径。
  /usr/local/venv/bin/python /tmp/relhelper/release_lib.py check-schema-stamp \
    --root "$SRC" "$SRC/openpilot/cereal/gen/cpp"
}

check_params_keys() {
  /usr/local/venv/bin/python /tmp/relhelper/release_lib.py check-params-keys "$SRC"
}

assemble_stage() {
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
}

stamp_tinygrad_pin_stage() {
  # pin 取自 lean-master 的 gitlink（ls-tree）——设备树可能是扁平消费者，
  # tinygrad_repo 无 .git，rev-parse 会穿透父仓库返回 release commit（垃圾 pin）
  python3 - "$SRC" "$STAGE" "refs/remotes/origin/$SRC_BRANCH" <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, "/tmp/relhelper")
import release_lib

sha = release_lib.stamp_tinygrad_pin(Path(sys.argv[2]), Path(sys.argv[1]), treeish=sys.argv[3])
print(f"[ok] tinygrad pin stamped: {sha[:7]}")
PYEOF
}

check_flat_tree() {
  # PC 路径守卫全量扫描：扁平树里所有 ELF 都不得含 /.comma 标记
  # （09-11 交叉构建事故的防线；设备本树构建的结构性产物，此处为回归防线）；
  # 且 stage 树必须能自行解析 tinygrad pin（发布门禁）。判据在
  # release_lib.check_flat_tree，壳只留调用。
  /usr/local/venv/bin/python /tmp/relhelper/release_lib.py check-flat-tree .
  echo "PC-path guard: all ELFs clean"
  echo "release gate: tinygrad pin resolvable in stage tree"
}

commit_release() {
  DATETIME=$(date '+%Y-%m-%dT%H:%M:%S')
  SRC_SHA=$(git -C "$SRC" rev-parse "$SRC_REF")
  git init -q -b "$RELEASE_BRANCH"
  git config user.name lochuan
  git config user.email lochuan@users.noreply.github.com
  # .overlay_init 是 updater 的运行时 overlay 标记（updated.py 管理、随更新周期
  # 创建/删除）——track 进 release commit 会让运行时删除变成"树脏"，每次重启后
  # 的发布都卡在前提检查。结构白名单仍保留该条目（防镜像删除误伤），但 git 不跟踪。
  git add -f . ':(exclude).overlay_init'
  git -c core.compression=0 -c gc.auto=0 commit -m "openpilot v$VERSION lean release (device-built, flat)

date: $DATETIME
source commit: $SRC_BRANCH@$SRC_SHA
built on: comma device"
}

run_stage "前提检查" check_prereqs
run_stage "同步 $SRC_BRANCH 源内容到设备树（结构性防漂移：发布树 ≡ $SRC_BRANCH + 产物）" sync_sources
run_stage "Materialize tinygrad at the $SRC_BRANCH gitlink" materialize_tinygrad
run_stage "Materialize 构建 gitlink（msgq/rednose/panda；扁平树只带运行时子集）" materialize_gitlinks
run_stage "全量重建 native 产物（ARTIFACT_PATHS）" rebuild_native
run_stage "内置 driving 模型（输入指纹未变则跳过重编）" compile_driving_pkl
run_stage "capnp schema/gen 一致性门禁" check_schema_stamp
run_stage "params 键表门禁：params_keys.h 的每个键必须已编译进 libparams_c.so" check_params_keys
run_stage "组装扁平树 stage" assemble_stage
run_stage "stamp tinygrad pin（发布树剥了 .git，这是选择器门控唯一可读的树 pin）" stamp_tinygrad_pin_stage
run_stage "stage 树发布门禁（PC 路径 ELF + tinygrad pin 可解析）" check_flat_tree
run_stage "touch prebuilt" touch prebuilt

VERSION=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' openpilot/sunnypilot/common/version.h | head -1)
run_stage "组发布 commit: openpilot v$VERSION lean release (device-built, flat)" commit_release

echo "[ok] 扁平树 stage 就绪: $STAGE（分支 $RELEASE_BRANCH, 单 commit），等 Mac 侧取走推送"
echo "    树大小: $(du -sh "$STAGE" | cut -f1)"
