#!/usr/bin/env bash
#
# build_orb_release.sh — OrbStack 容器构建 lean-sp release（mac 上跑）
#
# 镜像官方 tools/release/build_release.sh 流程，用 OrbStack Ubuntu 24.04 ARM64
# 容器构建（和 AGNOS 同 distro/GLIBC，产物直接可上设备）：
#   - 容器持久层：工具链 + uv venv 只装一次（首次 init），之后复用
#   - 每次构建：git 干净同步 + 清构建产物（.o/.so/__pycache__）+ scons -j$(nproc)
#   - SKIP_TINYGRAD_COMPILE=1（tinygrad ONNX 解析 bug，模型用 CI 预构建）
#   - 产物 rsync 到设备 /data/openpilot + 重启冒烟
#
# 用法（mac 上跑）:
#   ./tools/release/build_orb_release.sh
#
# 环境变量:
#   ORB_MACHINE   (默认 opilotbuild)   OrbStack 机器名
#   SOURCE_BRANCH (默认 lean-sp-master) 构建分支
#   DEVICE        (默认 comma@10.205.161.33) 目标设备
#   SKIP_DEVICE=1                       跳过推设备+重启（只构建）
#
set -e
set -x

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
SOURCE_DIR="$(git -C "$DIR" rev-parse --show-toplevel)"
ORB_MACHINE="${ORB_MACHINE:-opilotbuild}"
SOURCE_BRANCH="${SOURCE_BRANCH:-lean-sp-master}"
DEVICE="${DEVICE:-comma@10.205.161.33}"
DEVICE_DIR="/data/openpilot"

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
# clone（幂等：首次 clone，之后复用）
if [ ! -d "$HOME/opilot" ]; then
  echo "[init] cloning repo..."
  cd "$HOME" && git clone --recurse-submodules https://github.com/lochuan/lean-mici.git opilot
fi
# uv venv + 依赖（官方 uv sync --all-extras：读 pyproject.toml 全依赖，
# 依赖变化时自动同步，不手动列）
cd "$HOME/opilot"
export UV_PROJECT_ENVIRONMENT="$HOME/venv"
uv sync --all-extras
# 验证构建依赖
"$HOME/venv/bin/python" -c "
import SCons,acados,capnproto,eigen,ffmpeg,json11,ncurses,zeromq,zstandard,bootstrap_icons,raylib,imgui,msgq,opendbc,tinygrad
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

# --- 6. 产物 rsync 到设备（容器→设备 ssh 管道）---
if [ -z "$SKIP_DEVICE" ]; then
  echo "[-] rsync to device T=$SECONDS"
  # 容器 tar → mac ssh 管道 → 设备 tar 解包（容器和设备同 LAN）
  ssh "default@$ORB_MACHINE@orb" "tar czf - -C \$HOME/opilot \
    openpilot launch_chffrplus.sh launch_env.sh SConstruct SConscript 2>/dev/null" \
    | ssh "$DEVICE" "tar xzf - -C $DEVICE_DIR"
  ssh "$DEVICE" "
    cd $DEVICE_DIR
    ln -sfn msgq_repo/msgq msgq 2>/dev/null
    ln -sfn opendbc_repo/opendbc opendbc 2>/dev/null
    ln -sfn rednose_repo/rednose rednose 2>/dev/null
    ln -sfn tinygrad_repo/tinygrad tinygrad 2>/dev/null
    touch prebuilt
    echo '[device] activated'
  "

  # --- 7. 重启 comma + offroad 冒烟 ---
  echo "[-] restart comma T=$SECONDS"
  ssh "$DEVICE" '
    pkill -9 -f "manager.py" 2>/dev/null || true
    pkill -9 -f "hardwared.py|ui.py|statsd.py|models_manager|logmessaged|tombstoned|camerad|encoderd" 2>/dev/null || true
    sleep 2
    sudo systemctl restart comma
    sleep 30
    echo "[device] offroad smoke:"
    tmux capture-pane -t comma:0.0 -p 2>/dev/null | grep -vE "FPS dropped|raylib: FONT|^[0-9]+$|^$" | tail -5
  '
fi

echo "=== done T=$SECONDS ==="
