#!/usr/bin/env bash
#
# build_lean_release_on_orb.sh — OrbStack 容器构建 lean release（mac 上跑）
#
# 用 OrbStack Ubuntu 24.04 ARM64 容器出 lean release（替代上游 Jenkins 流程）
# 容器构建源码与 panda 固件；native ELF 运行时产物一律不来自容器，
# 只从 release/prebuilt/arm64 里经过校验后overlay：
#   - 容器持久层：工具链 + uv venv 只装一次（首次 init），之后复用
#   - 每次构建：git 干净同步 + 清构建产物 + scons（只编可交叉的目标）
#   - SKIP_TINYGRAD_COMPILE=1（模型 pkl 必须在设备上编，见下）
#   - 构建产物推 lean-release 分支（设备自己 checkout lean-release 跑）
#
# ── 哪些产物必须在 comma 设备上构建 ───────────────────────────────────────
# 1) native ELF（.so / 无后缀 daemon）：容器 glibc/ABI 与 AGNOS 不一致。
# 2) 驾驶模型 driving_tinygrad.pkl：内含 tinygrad 编译后的 JIT 内核。
#    SConscript 按 arch 选后端——设备是 DEV=QCOM，容器/mac 是 DEV=CPU，
#    因此容器编出来的 pkl 在设备上用不了。这也是保留 SKIP_TINYGRAD_COMPILE=1 的原因。
# 两类产物从设备构建后写入 release/prebuilt/arm64（存量存货，不再有抓取脚本），
# 由 release_lib.py 校验（ELF 查架构，pkl 查 sha256）后 overlay。
# native 输入变化导致存货过期时，release 自动降级 source-only，设备首启自愈。
#
# 可以交叉编译的：纯 Python、capnp 生成物、panda 固件（裸 .bin，不依赖 ABI）。
#
# 容器不再跑全量 scons。原因：SConstruct 靠构建机的 /AGNOS 判断 arch，容器里
# 没有 → __COMMA_HARDWARE__ 不定义 → hw.h 编译期选 Hardware::PC() 分支 →
# 把 $HOME/.comma/params 编进二进制。这种 ELF 架构合法，is_arm64_elf() 查不出，
# 曾导致 pandad 读错 params 目录、OBD 握手死锁、实车 "unavailable"。
# 现在 release_lib.py 的 PC 路径守卫会拒绝任何含 /.comma 的 native 产物。
#
# 防陈旧机制：release_lib.py 的 NATIVE_INPUT_PATHS 把上述产物的全部构建输入
# （含 driving_supercombo.onnx、compile_modeld.py、tinygrad_repo）纳入 native_hash。
# 任一输入变动 → hash 不匹配 → prebuilt 被拒 → 本次 release 不含 prebuilt，
# 设备首启自行 scons 全量重建（含 pkl）。切勿为了"构建更快"放宽这个校验。
#
# ── prebuilt 标记语义（v2026.003.017 起）─────────────────────────────────
# 标记【永远不随 release 发布】。设备运行时挣得：launch_chffrplus.sh 在
# 首启跑 build.py，编译成功才 touch prebuilt → 之后开机零编译。
# 全新安装/factory reset 必然重编一次 → fresh install 永不砖。
# 存货过期后不再有抓取脚本刷新：此后全新安装将保持全量编译（30-60 分钟），
# 设备自打标记后日常使用不受影响。
#
# 用法（mac 上跑）:
#   ./tools/release/build_lean_release_on_orb.sh
#
# 环境变量:
#   ORB_MACHINE    (默认 opilotbuild)   OrbStack 机器名
#   SOURCE_BRANCH  (默认 lean-master)   构建分支
#   RELEASE_BRANCH (默认 lean-release)  推目标分支
#   SKIP_SMOKE_GATE=1                   跳过设备 smoke 门禁（不推荐）
#   SMOKE_DURATION (默认 60)            smoke 时长（秒）
#
set -e
set -x

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
SOURCE_DIR="$(git -C "$DIR" rev-parse --show-toplevel)"

# ===== native 输入变更预警（提前喊话，避免只看到 tail 里的 source-only 信息）=====
release_lib="$SOURCE_DIR/tools/release/release_lib.py"
manifest="$SOURCE_DIR/release/prebuilt/arm64/MANIFEST"
if [ -f "$manifest" ]; then
  manifest_hash=$(sed -n 's/^native_hash=//p' "$manifest" 2>/dev/null | head -1)
  current_hash=$(python3 "$release_lib" hash HEAD 2>/dev/null || true)
  if [ -n "$manifest_hash" ] && [ "$manifest_hash" != "$current_hash" ]; then
    echo "====================================================================" >&2
    echo "⚠️  NATIVE INPUTS CHANGED（自上次 prebuilt 收割后）" >&2
    echo "⚠️  本轮 release 将 SOURCE-ONLY：设备首启 scons 全量编译" >&2
    echo "⚠️  30–60 分钟黑屏/风扇狂转，切勿断电（编译成功后设备自打 prebuilt 标记，之后恢复正常快启）" >&2
    echo "⚠️  后续全新安装将同样全量编译（无刷新机制，属接受的成本）" >&2
    echo "--------------------------------------------------------------------" >&2
    sleep 3
  fi
else
  echo "[native] release/prebuilt/arm64/MANIFEST 不存在：prebuilt 尚未收割，本轮将 SOURCE-ONLY" >&2
fi

ORB_MACHINE="${ORB_MACHINE:-opilotbuild}"
SOURCE_BRANCH="${SOURCE_BRANCH:-lean-master}"
RELEASE_BRANCH="${RELEASE_BRANCH:-lean-release}"
BUILD_BRANCH="build-mici"

# ===== 构建的是 origin/$SOURCE_BRANCH,不是本地 HEAD =====
# 容器 `reset --hard origin/$SOURCE_BRANCH`,所以未推送的本地提交不会进 release。
# 以前这只是文档里的一句提醒,结果就是"改完直接发版、发出去的还是上一版"。
# 这里做成硬检查。
echo "[-] verifying $SOURCE_BRANCH is pushed T=$SECONDS"
git -C "$SOURCE_DIR" fetch --quiet fork "$SOURCE_BRANCH" 2>/dev/null \
  || git -C "$SOURCE_DIR" fetch --quiet origin "$SOURCE_BRANCH" 2>/dev/null || true
local_head=$(git -C "$SOURCE_DIR" rev-parse HEAD)
remote_head=$(git -C "$SOURCE_DIR" rev-parse "refs/remotes/fork/$SOURCE_BRANCH" 2>/dev/null \
  || git -C "$SOURCE_DIR" rev-parse "refs/remotes/origin/$SOURCE_BRANCH" 2>/dev/null || echo "")
if [ "$local_head" != "$remote_head" ]; then
  ahead=$(git -C "$SOURCE_DIR" rev-list --count "${remote_head:-$local_head}..$local_head" 2>/dev/null || echo "?")
  echo "====================================================================" >&2
  echo "❌ FATAL: 本地 $SOURCE_BRANCH 未推送,容器会构建远端的旧提交" >&2
  echo "   local : $local_head" >&2
  echo "   remote: ${remote_head:-<none>}  (本地领先 $ahead 个提交)" >&2
  echo "   先推送:  git push fork $SOURCE_BRANCH" >&2
  echo "====================================================================" >&2
  exit 1
fi
# 未提交的改动同样不会进 release,静默丢弃比失败更危险。
if ! git -C "$SOURCE_DIR" diff --quiet HEAD -- || \
   [ -n "$(git -C "$SOURCE_DIR" ls-files --others --exclude-standard -- openpilot tools release)" ]; then
  echo "⚠️  工作区有未提交改动,它们不会进本次 release(容器只认 origin/$SOURCE_BRANCH)" >&2
  git -C "$SOURCE_DIR" status --short | head -10 >&2
  sleep 3
fi
echo "[ok] $SOURCE_BRANCH == origin @ $local_head"

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

# --- 4. scons 构建（只做可交叉编译的产物；native ELF 一律来自设备）---
#
# 为什么不再跑全量 `scons -j$(nproc)`：
# SConstruct 用构建机上的 /AGNOS 来决定 arch（COMMA_HARDWARE），容器里没有
# /AGNOS，于是 __COMMA_HARDWARE__ 不定义，common/hardware/hw.h 在编译期选中
# Hardware::PC() 分支，把 "$HOME/.comma/params" 编进二进制（设备上应是
# /data/params）。这种产物依然是合法的 ARM64 ELF，is_arm64_elf() 查不出来。
#
# 2026-09-11 实车故障就是这么来的：容器编的 pandad 读
# /home/comma/.comma/params/d/，而 card 写 /data/params/d/，OBD multiplexing
# 握手永远完不成 → sendcan=0，VIN/FW 一条没发 → CarParams 永不发布 →
# selfdrived/controlsd/plannerd/radard 全卡在 waiting for CarParams →
# 屏幕停在 "sunnypilot unavailable, waiting to start"。
#
# 所以容器只构建不依赖 ABI 的东西：capnp 代码生成（纯 Python 导入需要）。
# 全部 native ELF/.so 与模型 pkl 来自 release/prebuilt 存货（设备构建），
# 经 release_lib.py 校验（含 PC 路径守卫）后 overlay。
echo "[-] scons build (cross-safe targets only) T=$SECONDS"
$SSH '
set -e
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/opilot"
source "$HOME/venv/bin/activate"
export PYTHONPATH="$HOME/opilot"
export SKIP_TINYGRAD_COMPILE=1
# capnp 代码生成：纯代码生成，无 ABI 耦合，设备端 Python 导入依赖它。
time scons -j$(nproc) openpilot/cereal/
echo "[build] scons (cereal/capnp) OK"
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
set -e
cd "$HOME/opilot"
status=0
for sm in msgq_repo opendbc_repo panda rednose_repo tinygrad_repo; do
  dirty=$(git -C "$sm" status --porcelain | grep -E "\.so$|\.bin$|\.bin\.signed$|\.elf$" || true)
  if [ -n "$dirty" ]; then
    echo "[FATAL] submodule $sm 构建产物未提交进 fork 仓库（设备端拿不到）：" >&2
    echo "$dirty" >&2
    status=1
  fi
done
if [ "$status" -ne 0 ]; then
  exit "$status"
fi
echo "[guard] submodule artifact check OK"
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

# LFS guard：媒体已 de-LFS（全部进普通 git blob，lean-master e12a3c057），
# 保留 sweep 作回归防线：今后任何人把指针假文件带进 release worktree 直接 FATAL
if python3 \$HOME/opilot/tools/release/release_lib.py sweep-lfs-pointers /tmp/opilot-release; then
  echo \"[release] LFS media sweep OK\"
else
  echo \"[release] FATAL: LFS pointer stubs in release tree; fix .gitattributes / git lfs fetch\" >&2
  exit 1
fi

# 只 overlay panda 固件（裸二进制，非 ELF，不依赖容器 ABI）。
# native ELF 运行时产物一律来自 release/prebuilt/arm64，由 release_lib.py 校验。
(cd \$HOME/opilot && find . \( -name \"*.bin\" -o -name \"*.bin.signed\" \) -not -path \"./.git/*\" -print0 | tar --null -T - -cf -) | tar -x -C /tmp/opilot-release

rm -f /tmp/opilot-no-prebuilt
if python3 \$HOME/opilot/tools/release/release_lib.py overlay /tmp/opilot-release; then
  echo \"[release] prebuilt shipped\"
else
  echo \"[release] ⚠️⚠️⚠️ SOURCE-ONLY RELEASE: 设备首启将 scons 全量编译 30–60 分钟（黑屏/风扇狂转，切勿断电）\" >&2
  echo \"[release] ⚠️ 设备编译成功后自打 prebuilt 标记恢复正常快启（runtime-earned,无需干预）\" >&2
  echo \"[release] ⚠️ 后续全新安装将同样全量编译（无刷新机制，属接受的成本）\" >&2
  echo \"[release] WARN: prebuilt validation failed, shipping source-only release\" >&2
  touch /tmp/opilot-no-prebuilt
  rm -rf /tmp/opilot-release/release/prebuilt
fi

cd /tmp/opilot-release
VERSION=\$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' openpilot/sunnypilot/common/version.h | head -1)
git -c core.compression=0 -c gc.auto=0 add -f .
if git diff --cached --quiet; then
  echo \"[release] no changes to commit\"
else
  git -c core.compression=0 -c gc.auto=0 commit -m \"openpilot v\$VERSION lean release\"
fi

# push 到 fork 的 lean-release 分支（origin = lochuan/lean-mici）
# 100MB 大 commit，GitHub 可能限流断连，重试 3 次
pushed=0
for i in 1 2 3; do
  if git push -f origin HEAD:$RELEASE_BRANCH 2>&1; then
    echo \"[release] pushed $RELEASE_BRANCH (attempt \$i)\"
    pushed=1
    break
  fi
  echo \"[release] push attempt \$i failed, retrying...\" >&2
  sleep 5
done
if [ \"\$pushed\" -ne 1 ]; then
  echo \"FATAL: failed to push $RELEASE_BRANCH after 3 attempts\" >&2
  git worktree remove --force /tmp/opilot-release 2>/dev/null || true
  exit 1
fi
git rev-parse HEAD > /tmp/opilot-release-commit
git worktree remove --force /tmp/opilot-release 2>/dev/null || true
"

# 记下刚推出去的 release commit,交给 smoke gate 校验设备确实更新到了它
RELEASE_COMMIT=$($SSH 'cat /tmp/opilot-release-commit 2>/dev/null' | tr -d '\r\n' || true)
echo "[release] $RELEASE_BRANCH = ${RELEASE_COMMIT:-<unknown>}"

echo "=== done T=$SECONDS ==="

# --- 6.5 发布门禁：设备 onroad smoke 必须通过 ---
# 2026-09-09 实车事故：smoke 已报 SMOKE: FAIL（selfdriveState msgs: 0），
# 但没人看结果，坏包照样上了车。默认强制执行；SKIP_SMOKE_GATE=1 可显式跳过。
if [ "${SKIP_SMOKE_GATE:-0}" = "1" ]; then
  echo "[release] WARN: smoke gate skipped by SKIP_SMOKE_GATE=1" >&2
else
  echo "[-] smoke gate T=$SECONDS"
  # EXPECT_COMMIT 让门禁先确认设备真的在这次 release 上;否则它测的是上一版,
  # 报出来的 PASS 毫无意义(这正是 2026-09-09 事故的形态)。
  # 设备侧的更新是人工步骤,所以这里失败是预期的提示,不是构建坏了。
  if ! EXPECT_COMMIT="$RELEASE_COMMIT" "$DIR/smoke_gate.sh" "${SMOKE_DURATION:-60}"; then
    echo "FATAL: smoke gate failed — release 已推送但请勿上车,先修复" >&2
    exit 1
  fi
fi

# --- 7. 结尾汇总：prebuilt 校验失败时，说明后果 ---
if $SSH 'test -f /tmp/opilot-no-prebuilt' 2>/dev/null; then
  cat <<'BANNER'

********************************************************************************
*  ⚠️  本次 release 未包含 prebuilt（native 产物校验失败或源码已变化）
*
*  后果：设备更新后首次开机，build.py 会原生重建全部 native 产物（30-60 分钟，
*  慢一次），成功后自打 prebuilt 标记恢复正常快启，无需任何手动干预。
*  此后所有全新安装同样全量编译（无存货刷新机制，属接受的成本）。
********************************************************************************
BANNER
fi
