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
find . \( -name "*.o" -o -name "*.so" -o -name "*.pyc" -o -name "*.a" -o -name "*.os" \) \
  -not -path "./.git/*" -delete 2>/dev/null || true
find . -name "__pycache__" -not -path "./.git/*" -type d -exec rm -rf {} + 2>/dev/null || true
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

# --- 6. push lean-release 分支（容器 worktree 出 release commit）---
echo "[-] push $RELEASE_BRANCH T=$SECONDS"
$SSH "
set -e
cd \$HOME/opilot
# worktree 隔离 release commit（不污染 dev 树）
if git worktree remove --force /tmp/opilot-release 2>/dev/null; then :; fi
git worktree prune
git update-ref -d refs/heads/$BUILD_BRANCH 2>/dev/null || true
git update-ref refs/heads/$BUILD_BRANCH HEAD
git worktree add --detach /tmp/opilot-release $BUILD_BRANCH
# 从 SOURCE_BRANCH 取干净源码（git archive，无 submodule/LFS/未跟踪文件）
git archive $SOURCE_BRANCH | tar -x -C /tmp/opilot-release
# release 标记
touch /tmp/opilot-release/prebuilt
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
