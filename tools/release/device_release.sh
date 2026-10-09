#!/usr/bin/env bash
#
# device_release.sh — 在 comma 设备上从干净构建树构建扁平 release。
#
# 构建树 /data/build 与运行树 /data/openpilot 分离：每次构建先把 /data/build
# 和各子模块 checkout --force 到源 commit 并 git clean -ffdx，树内容 ≡ 源 commit；
# 产物全部由 scons --minimal 全量构建（含 cereal gen、panda 固件、driving pkl），
# 未变的部分由 /data/scons_cache 秒级取回。发布树 = 构建树剥离构建输入与中间产物。
#
# 用法（设备上，由 publish_release_from_device.sh scp 后 nohup 执行）:
#   bash device_release.sh
# 产出: /data/relstage（单孤儿 commit 的 git 仓库，分支 $RELEASE_BRANCH），由 Mac 侧取走推送。
#
# 分支参数（环境变量，缺省 = 主线发版）:
#   SRC_BRANCH     源分支，缺省 lean-master
#   RELEASE_BRANCH 发布分支，缺省 lean-release
# 例: SRC_BRANCH=big-uplink RELEASE_BRANCH=big-release bash device_release.sh
set -Eeuo pipefail   # -E：ERR trap 继承进阶段函数

BUILD=/data/build
STAGE=/data/relstage
REPO_URL=https://github.com/lochuan/lean-mici.git
SRC_BRANCH="${SRC_BRANCH:-lean-master}"
RELEASE_BRANCH="${RELEASE_BRANCH:-lean-release}"
# 默认 2GB 会按 mtime 淘汰掉 ~71M 的 driving pkl，下次发版白编 ~11 分钟
SCONS_CACHE_LIMIT=8e9
export PATH="/usr/local/venv/bin:$PATH"

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
  # 命令替换/子 shell 内的失败只向上传播退出码，话术在最外层报一次
  if [ "${BASH_SUBSHELL:-0}" -gt 0 ]; then
    exit 1
  fi
  die "${CURRENT_STAGE:-发布管线} 失败，拒绝发布（/data/build 保留现场）"
}
trap on_err ERR

run_stage() {
  # run_stage "标题" 命令 [参数...]
  CURRENT_STAGE="$1"
  echo "[-] $1 T=$SECONDS"
  shift
  "$@"
  CURRENT_STAGE=""
}

wake_cpu_cores() {
  # mici 省电时 4~7 号核下线；scons -j8 与 pkl 编译的 taskset -c 4 都要它们在线
  local n
  for n in 4 5 6 7; do
    if [ "$(cat /sys/devices/system/cpu/cpu$n/online 2>/dev/null)" = "0" ]; then
      echo 1 | sudo tee /sys/devices/system/cpu/cpu$n/online >/dev/null
    fi
  done
}

git_fetch_retry() {
  # git_fetch_retry <git 参数...> —— 设备到 GitHub 的 TLS 偶发握手失败，重试 3 次
  local i
  for i in 1 2 3; do
    git "$@" && return 0
    sleep 5
  done
  return 1
}

ensure_repo() {
  # ensure_repo <目录> <url> —— 目录不是 git 仓库则新建；origin 一律对齐到 url
  local dir="$1" url="$2"
  [ -d "$dir/.git" ] || { rm -rf "$dir"; git init -q "$dir"; git -C "$dir" remote add origin "$url"; }
  git -C "$dir" remote set-url origin "$url"
}

checkout_repo() {
  # checkout_repo <目录> <url> <sha> —— 浅取 sha，checkout --force + clean -ffdx：目录内容 ≡ sha。
  # fetch 一律 -4：设备走 IPv6 到 GitHub 时 TLS 反复断开（submodule update 传不了 -4，故逐个处理）
  local dir="$1" url="$2" sha="$3"
  ensure_repo "$dir" "$url"
  if ! git -C "$dir" cat-file -e "$sha^{commit}" 2>/dev/null; then
    git_fetch_retry -C "$dir" fetch -4 -q --depth=1 origin "$sha" || die "$dir fetch $sha 失败（3 次重试后）"
  fi
  git -C "$dir" checkout -q --force --detach "$sha"
  git -C "$dir" clean -ffdxq
}

checkout_source() {
  ensure_repo "$BUILD" "$REPO_URL"
  cd "$BUILD"
  git_fetch_retry fetch -4 -q --depth=1 origin "$SRC_BRANCH" || die "$SRC_BRANCH fetch 失败（3 次重试后）"
  SRC_SHA=$(git rev-parse FETCH_HEAD)
  checkout_repo "$BUILD" "$REPO_URL" "$SRC_SHA"
  # 先取进变量：进程替换里的失败不触发 set -e，会静默跳过所有子模块
  local submodules key path name
  submodules=$(git config -f .gitmodules --get-regexp '\.path$')
  [ -n "$submodules" ] || die ".gitmodules 里没有子模块，拒绝发布"
  while read -r key path; do
    name=${key#submodule.}
    name=${name%.path}
    checkout_repo "$BUILD/$path" "$(git config -f .gitmodules "submodule.$name.url")" "$(git rev-parse "HEAD:$path")"
  done <<< "$submodules"
  echo "[ok] 构建树 ≡ $SRC_BRANCH@${SRC_SHA:0:12}（含子模块）"
}

build() {
  wake_cpu_cores
  cd "$BUILD"
  PYTHONPATH="$BUILD:$BUILD/openpilot" scons -j8 --minimal cache_size_limit="$SCONS_CACHE_LIMIT" \
    || die "scons 构建失败，拒绝发布"
  echo "[ok] 构建完成 T=$SECONDS"
}

assemble_stage() {
  sudo rm -rf "$STAGE"
  mkdir -p "$STAGE"
  rsync -a "$BUILD/" "$STAGE/" \
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
    --exclude='/release/' \
    --exclude='*.onnx' \
    --exclude='*.onnx.data' \
    --exclude=node_modules
  cd "$STAGE"
  rm -rf tinygrad_repo/examples tinygrad_repo/test tinygrad_repo/docs \
         tinygrad_repo/scripts tinygrad_repo/.github
  find openpilot/third_party \( -iname '*x86*' -o -iname '*darwin*' \) -exec rm -rf {} + 2>/dev/null || true
  python3 "$BUILD/tools/release/release_lib.py" stamp-tinygrad-pin "$STAGE" "$BUILD"
  python3 "$BUILD/tools/release/release_lib.py" check-stage "$STAGE"
  touch prebuilt
}

commit_release() {
  cd "$STAGE"
  VERSION=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' openpilot/sunnypilot/common/version.h | head -1)
  git init -q -b "$RELEASE_BRANCH"
  git config user.name lochuan
  git config user.email lochuan@users.noreply.github.com
  git add -f .
  git -c core.compression=0 -c gc.auto=0 commit -q -m "openpilot v$VERSION lean release (device-built, flat)

date: $(date '+%Y-%m-%dT%H:%M:%S')
source commit: $SRC_BRANCH@$SRC_SHA
built on: comma device"
}

# 构建期间停 comma：3.5G 内存扛不住 scons -j8 + 运行中的进程，pkl 编译还要抢 GPU
run_stage "停 comma" sudo systemctl stop comma
run_stage "构建树 checkout $SRC_BRANCH（含子模块，clean -ffdx）" checkout_source
run_stage "scons --minimal 全量构建" build
run_stage "组装扁平树 stage（剥离 + tinygrad pin + 发布门禁）" assemble_stage
run_stage "组发布 commit" commit_release

echo "[ok] 扁平树 stage 就绪: $STAGE（分支 $RELEASE_BRANCH, 单 commit），等 Mac 侧取走推送"
echo "    树大小: $(du -sh "$STAGE" | cut -f1)"
