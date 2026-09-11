#!/usr/bin/env bash
#
# build_lean_release_on_orb.sh — OrbStack 容器构建 lean release（mac 上跑）
#
# 镜像官方 tools/release/build_release.sh 流程，用 OrbStack Ubuntu 24.04 ARM64
# 容器构建（和 AGNOS 同 distro/GLIBC，产物直接可上设备）：
#   - 容器持久层：工具链 + uv venv 只装一次（首次 init），之后复用
#   - 每次构建：git 干净同步 + 清构建产物（.o/.so/__pycache__）+ scons -j$(nproc)
#   - SKIP_TINYGRAD_COMPILE=1（tinygrad ONNX 解析 bug，模型用 CI 预构建）
#   - 构建产物推 lean-release 分支（设备自己 checkout lean-release 跑）
#
# 用法（mac 上跑）:
#   ./tools/release/build_lean_release_on_orb.sh
#
# 环境变量:
#   ORB_MACHINE    (默认 opilotbuild)   OrbStack 机器名
#   SOURCE_BRANCH  (默认 lean-master)   构建分支
#   RELEASE_BRANCH (默认 lean-release)  推目标分支
#
set -e
set -x

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
SOURCE_DIR="$(git -C "$DIR" rev-parse --show-toplevel)"
ORB_MACHINE="${ORB_MACHINE:-opilotbuild}"
SOURCE_BRANCH="${SOURCE_BRANCH:-lean-master}"
RELEASE_BRANCH="${RELEASE_BRANCH:-lean-release}"
BUILD_BRANCH="build-mici"

echo "=== OrbStack lean release build === T=$SECONDS"
echo "SOURCE_DIR=$SOURCE_DIR"
echo "ORB_MACHINE=$ORB_MACHINE"
echo "SOURCE_BRANCH=$SOURCE_BRANCH"

# --- 0. OrbStack 机器检查/创建（首次）---
echo "[-] checking orb machine T=$SECONDS"
if ! orb list 2>/dev/null | awk '{print $1}' | grep -qx "$ORB_MACHINE"; then
  echo "creating $ORB_MACHINE (ubuntu:24.04 arm64, 8G/4cpu/32G)"
  orb create -a arm64 --memory 8G --cpus 4 --disk 32G ubuntu:24.04 "$ORB_MACHINE"
fi

SSH="ssh default@$ORB_MACHINE@orb"

# --- 1. 容器初始化（首次：工具链 + uv venv + 依赖）---
echo "[-] container init (first-time only) T=$SECONDS"
$SSH '
set -e
export PATH="$HOME/.local/bin:$PATH"
# 工具链（幂等：缺啥装啥）
if ! command -v git >/dev/null; then
  echo "[init] installing apt packages..."
  sudo apt-get update -qq
  sudo apt-get install -y -qq git build-essential python3-venv gdb gcc-arm-none-eabi
fi
# uv（幂等）
if [ ! -f "$HOME/.local/bin/uv" ]; then
  echo "[init] installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# git 身份 + SSH remote（push 认证，幂等）
git config --global user.email "kevin@lochuan.dev" 2>/dev/null || true
git config --global user.name "kevin" 2>/dev/null || true
# clone（幂等：首次 clone，之后复用）
if [ ! -d "$HOME/opilot" ]; then
  echo "[init] cloning repo..."
  cd "$HOME" && git clone --recurse-submodules git@github.com:lochuan/lean-mici.git opilot
  git -C "$HOME/opilot" remote set-url origin git@github.com:lochuan/lean-mici.git
fi
# uv venv + 依赖（官方 uv sync --all-extras：读 pyproject.toml 全依赖，
# 依赖变化时自动同步，不手动列）
cd "$HOME/opilot"
export UV_PROJECT_ENVIRONMENT="$HOME/venv"
uv sync --all-extras
# 验证构建依赖（msgq 的 C 扩展 ipc_pyx 由 scons 构建，init 阶段不检查）
"$HOME/venv/bin/python" -c "
import SCons,acados,capnproto,eigen,ffmpeg,json11,ncurses,zeromq,zstandard,bootstrap_icons,raylib,imgui,opendbc,tinygrad
print(\"[init] deps OK\")
"
echo "[init] done"
'

# --- 2. 每次构建：git 干净同步 ---
echo "[-] clean git sync T=$SECONDS"
$SSH "
set -e
export PATH=\$HOME/.local/bin:\$PATH
cd \$HOME/opilot
git fetch origin $SOURCE_BRANCH
git checkout -f $SOURCE_BRANCH
git reset --hard origin/$SOURCE_BRANCH
# 清理已删 submodule 残留（如 teleoprtc_repo）
git submodule deinit -f teleoprtc_repo 2>/dev/null || true
git rm -f teleoprtc_repo 2>/dev/null || true
rm -rf .git/modules/teleoprtc_repo 2>/dev/null || true
git submodule update --init --recursive
echo \"[sync] at \$(git rev-parse --short HEAD) \$(git branch --show-current)\"
"

# --- 3. 每次构建：清构建产物（干净环境）---
echo "[-] clean build artifacts T=$SECONDS"
$SSH '
set -e
cd "$HOME/opilot"
find . \( -name "*.o" -o -name "*.so" -o -name "*.so.*" -o -name "*.pyc" -o -name "*.a" -o -name "*.os" \) \
  -not -path "./.git/*" -delete 2>/dev/null || true
find . -name "__pycache__" -not -path "./.git/*" -type d -exec rm -rf {} + 2>/dev/null || true
# 无后缀 ELF 可执行文件 + 版本化 .so.X 也要清（如 loggerd/encoderd/libqpOASES_e.so.3.1）：
# lean 裁剪删掉 SConscript target 后旧二进制会残留（encoderd 就是这么混进 release 的）。
# 只清主仓库，不动 submodule（fork 里有 tracked 产物，由 scons 全量重建覆盖）。
python3 tools/release/elf_find.py \
  | grep -zv -E "^\./(msgq_repo|opendbc_repo|panda|rednose_repo|tinygrad_repo|openpilot/sunnypilot/neural_network_data)/" \
  | xargs -0r rm -f
rm -f .sconsign.dblite 2>/dev/null || true
echo "[clean] build artifacts removed"
'

# --- 4. scons 构建（SKIP_TINYGRAD_COMPILE 跳过 tinygrad ONNX bug）---
echo "[-] scons build T=$SECONDS"
$SSH '
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/opilot"
source "$HOME/venv/bin/activate"
export PYTHONPATH="$HOME/opilot"
export SKIP_TINYGRAD_COMPILE=1
time scons -j$(nproc)
echo "[build] scons OK"
'

# --- 5. panda build（debug，匹配 AGNOS 当前 panda）---
echo "[-] panda build T=$SECONDS"
$SSH '
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/opilot"
source "$HOME/venv/bin/activate"
export PYTHONPATH="$HOME/opilot"
scons -j$(nproc) panda/
echo "[build] panda OK"
'

# --- 5.5 submodule 产物守卫：bump 指针后必须把新构建产物提交进 fork 仓库，
#     否则设备端 submodule update --init 拿到的是 fork 里旧的/缺失的 .so/.bin ---
echo "[-] submodule artifact guard T=$SECONDS"
$SSH '
cd $HOME/opilot
status=0
for sm in msgq_repo opendbc_repo panda rednose_repo tinygrad_repo; do
  dirty=$(git -C "$sm" status --porcelain | grep -E "\.so$|\.bin$|\.bin\.signed$|\.elf$" || true)
  if [ -n "$dirty" ]; then
    echo "[warn] submodule $sm 构建产物未提交进 fork 仓库（设备端拿不到）："
    echo "$dirty"
    status=1
  fi
done
echo "[guard] submodule artifact check done (status=$status)"
'

# --- 6. push lean-release 分支（容器 worktree 出 release commit）---
echo "[-] push $RELEASE_BRANCH T=$SECONDS"
$SSH "
set -e
set -o pipefail
cd \$HOME/opilot
# worktree 隔离 release commit（不污染 dev 树）
if git worktree remove --force /tmp/opilot-release 2>/dev/null; then :; fi
git worktree prune
git update-ref -d refs/heads/$BUILD_BRANCH 2>/dev/null || true
git update-ref refs/heads/$BUILD_BRANCH HEAD
git worktree add --detach /tmp/opilot-release $BUILD_BRANCH
# 叠加构建产物：全部 ELF（含无后缀 daemon、版本化 .so.X）+ panda 固件 .bin/.bin.signed（裸二进制非 ELF）。
# 产物 tracked 进 release 分支：设备 OTA 的 git clean -xdff 会删未跟踪/被忽略文件（updated.py fetch_update）。
# 注意：不推 prebuilt——camerad 等 comma_arm64 专属可执行文件容器构建不出（无 /AGNOS、无 QCOM 相机栈），
# 必须靠设备端 build.py 首启构建（/data/scons_cache 有缓存，很快）。
# loggerd 同理排除：容器版链接容器 ffmpeg（libav*.so.61），AGNOS 上无对应库（实测 exit 127 起不来），
# 交由设备端 build.py 原生链接重建。
(cd \$HOME/opilot && { find . \( -name \"*.bin\" -o -name \"*.bin.signed\" \) -not -path \"./.git/*\" -print0; python3 tools/release/elf_find.py; } | grep -zv -F -e './openpilot/system/loggerd/loggerd' -e './openpilot/system/loggerd/encoderd' | tar --null -T - -cf -) | tar -x -C /tmp/opilot-release
# 产物兜底校验：缺失说明叠加失败，拒绝推裸源码 release
test -f /tmp/opilot-release/openpilot/common/libparams_c.so || { echo \"FATAL: build artifact overlay failed\"; exit 1; }
# 回归守卫：被裁剪/ABI 不兼容的二进制不得混入 release
test ! -e /tmp/opilot-release/openpilot/system/loggerd/encoderd || { echo \"FATAL: stale encoderd leaked into release\"; exit 1; }
test ! -e /tmp/opilot-release/openpilot/system/loggerd/loggerd || { echo \"FATAL: container loggerd leaked into release\"; exit 1; }
# 回收产物 overlay：设备原生构建的 camerad/loggerd（camerad 容器构建不出；
# loggerd 容器版 ABI 不兼容已在上面排除）。native 源码树哈希未变 → 随 release 发
# prebuilt 标记，设备开机跳过 build.py（省 ~9s）；哈希不匹配 → 回退设备端首启原生重建。
PREBUILT_DIR=release/prebuilt/arm64
if [ -f /tmp/opilot-release/\$PREBUILT_DIR/MANIFEST ]; then
  want=\$(grep '^native_hash=' /tmp/opilot-release/\$PREBUILT_DIR/MANIFEST | cut -d= -f2)
  have=\$(\$HOME/opilot/tools/release/prebuilt_native_hash.sh HEAD)
  if [ \"\$want\" = \"\$have\" ]; then
    for f in openpilot/system/camerad/camerad openpilot/system/loggerd/loggerd; do
      cp /tmp/opilot-release/\$PREBUILT_DIR/\$f /tmp/opilot-release/\$f
    done
    touch /tmp/opilot-release/prebuilt
    echo \"[release] prebuilt shipped (native sources unchanged)\"
  else
    echo \"[release] WARN: native sources changed since harvest, no prebuilt (device rebuilds on first boot)\"
  fi
fi
# release 分支不带回收暂存目录（只带就位后的二进制）
rm -rf /tmp/opilot-release/release/prebuilt
cd /tmp/opilot-release
# release commit
VERSION=\$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' openpilot/sunnypilot/common/version.h | head -1)
git -c core.compression=0 -c gc.auto=0 add -f .
git -c core.compression=0 -c gc.auto=0 commit -m \"openpilot v\$VERSION lean release\" || true
# push 到 fork 的 lean-release 分支（origin = lochuan/lean-mici）
# 100MB 大 commit，GitHub 可能限流断连，重试 3 次
# 注意：release commit 在 worktree 的 detached HEAD 上，推 HEAD 而不是 build-mici
for i in 1 2 3; do
  if git push -f origin HEAD:$RELEASE_BRANCH 2>&1; then
    echo \"[release] pushed $RELEASE_BRANCH (attempt $i)\"
    break
  fi
  echo \"[release] push attempt $i failed, retrying...\"
  sleep 5
done
git worktree remove --force /tmp/opilot-release 2>/dev/null || true
"

echo "=== done T=$SECONDS ==="
