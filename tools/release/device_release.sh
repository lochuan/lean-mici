#!/usr/bin/env bash
#
# device_release.sh — 在 comma 设备上发布 lean-release（设备 = 唯一构建机）。
#
# 设计口径（2026-09-22 定稿，参考上游 sunnypilot-build-prebuilt.yaml 的
# "comma 设备即构建机" 模型 + build_stripped.sh 的提交形状）：
#   * lean-release 必须出自"已经在设备上正常运行"的构建：发布前先跑 60s 冒烟，
#     PASS 才继续。
#   * release commit 携带全部预编译产物（14 个 native ELF + driving pkl 分块 +
#     yolo pkl 及其 pin 侧车），经 release_lib.py 三重校验
#     （ELF 架构 / PC 路径守卫 / sha256 / native_hash）。
#   * Mac 侧容器交叉编译已移除（2026-09-22）。设备无 GitHub 推送凭据，
#     推送由 Mac 中继（见 publish_release_from_device.sh）。
#
# 用法（设备上，cwd=/data/openpilot）:  bash tools/release/device_release.sh
# 产出: 本地分支 device-release（= origin/lean-master + 产物 overlay），由 Mac 侧取走推送。
set -euo pipefail

BRANCH_TIP_REF="origin/lean-master"
RELWT=/data/relwt
DEVICE_BRANCH=device-release

echo "[-] 前提检查"
sudo systemctl is-active --quiet comma || { echo "comma 未运行（产物必须来自正在运行的构建）" >&2; exit 1; }
[ -z "$(git status --porcelain | grep -v '^??')" ] || { echo "工作树有未提交修改，拒绝发布" >&2; exit 1; }

echo "[-] 60s 冒烟（确定设备正常运行）T=$SECONDS"
sudo systemctl stop comma
SMOKE_RC=0
PYTHONPATH=/data/openpilot:/data/openpilot/openpilot \
  /usr/local/venv/bin/python tools/release/smoke_onroad_device.py 60 || SMOKE_RC=$?
if [ "$SMOKE_RC" -ne 0 ]; then
  echo "smoke FAIL — 设备未正常运行，拒绝发布" >&2
  sudo systemctl start comma
  exit 1
fi
echo "[ok] smoke PASS"

echo "[-] 拉取 lean-master 对象"
git fetch origin lean-master:refs/remotes/origin/lean-master

# 原生输入一致性：设备的产物是本树现编的，而 release commit 的原生内容必须
# 与 lean-master tip 相同（release/prebuilt 不在 NATIVE_INPUT_PATHS，parity 不受
# 产物影响）。不一致 = lean-master 已前进，先更新设备到最新 lean-release 再发布。
HASH_MASTER=$(python3 tools/release/release_lib.py hash origin/lean-master)
HASH_DEVICE=$(python3 tools/release/release_lib.py hash HEAD)
if [ "$HASH_MASTER" != "$HASH_DEVICE" ]; then
  echo "设备产物基于的原生输入与 lean-master 不一致（device=$HASH_DEVICE master=$HASH_MASTER）；先更新设备到最新 lean-release 再发布" >&2
  sudo systemctl start comma
  exit 1
fi

echo "[-] 收集产物到 release/prebuilt/arm64（设备树内，不建 worktree）"
PRE="release/prebuilt/arm64"
mkdir -p "$PRE"
for rel in $(python3 tools/release/release_lib.py artifact-paths); do
  [ -f "$rel" ] || { echo "设备缺少产物: $rel（先让它编译出来）" >&2; exit 1; }
  mkdir -p "$PRE/$(dirname "$rel")"
  cp "$rel" "$PRE/$rel"
done
python3 tools/release/release_lib.py data-artifact-globs | while read -r pat; do
  for f in $pat; do
    [ -f "$f" ] || continue
    mkdir -p "$PRE/$(dirname "$f")"
    cp "$f" "$PRE/$f"
  done
done

echo "[-] 写 MANIFEST + 三重校验"
NATIVE_HASH=$(python3 tools/release/release_lib.py hash HEAD)
SRC_COMMIT=$(git rev-parse origin/lean-master)
python3 tools/release/release_lib.py write-manifest "$SRC_COMMIT" "$NATIVE_HASH"
python3 tools/release/release_lib.py validate-artifacts

VERSION=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' openpilot/sunnypilot/common/version.h | head -1)
echo "[-] 组 release commit: openpilot v$VERSION lean release (device-built)"
# LFS 指针扫描（上游 publish.sh 的 submodule/LFS 防线等价物；本仓库已全 blob 化，
# 这里是回归防线：任何指针假文件混进产物直接 FATAL）
python3 tools/release/release_lib.py sweep-lfs-pointers .
# 溯源写入 commit message（上游 publish.sh 模式）：version / date / master commit
DATETIME=$(date '+%Y-%m-%dT%H:%M:%S')
git add -f "$PRE"
git -c user.name=lochuan -c user.email=lochuan@users.noreply.github.com \
   -c core.compression=0 -c gc.auto=0 commit -m "openpilot v$VERSION lean release (device-built)

date: $DATETIME
master commit: $SRC_COMMIT
built on: comma device (smoke-verified before publish)"
echo "[ok] 本地 lean-release = $(git rev-parse HEAD)（线性 release 历史），等 Mac 侧中继推送"

echo "[-] 恢复设备运行"
sudo systemctl start comma
