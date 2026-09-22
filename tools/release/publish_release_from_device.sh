#!/usr/bin/env bash
#
# publish_release_from_device.sh — Mac 侧编排：让 comma 设备构建并发布 lean-release。
#
# 分工（2026-09-22 定稿）：
#   * 设备 = 唯一构建机：冒烟验证运行正常 → 收集产物 → release_lib 三重校验 →
#     直接提交到本地 lean-release（线性 release 历史）。见 device_release.sh。
#   * 本脚本：ssh 触发设备流程 → 从设备 git 取本地 lean-release → 中继推送到
#     fork/lean-release（设备无 GitHub 推送凭据）→ 输出 release sha 供冒烟门禁。
#   * Mac 侧容器交叉编译已移除（2026-09-22）：设备是唯一构建机。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
DEVICE="${DEVICE:-comma@10.0.0.27}"
DEVICE_REPO="ssh://$DEVICE/data/openpilot"

echo "[-] 设备端构建与本地发布（含 60s 冒烟，设备会短暂停 openpilot）"
ssh "$DEVICE" 'cd /data/openpilot && bash -s' < "$DIR/device_release.sh"

echo "[-] 从设备取 lean-release"
git fetch "$DEVICE_REPO" +lean-release:refs/temp/device-release 2>&1 | tail -1
RELEASE_SHA=$(git rev-parse refs/temp/device-release)

echo "[-] 中继推送到 fork/lean-release"
PUSHED=0
for _ in 1 2 3; do
  if git push -f fork refs/temp/device-release:lean-release 2>&1 | tail -1; then
    PUSHED=1
    break
  fi
  sleep 5
done
if [ "$PUSHED" -ne 1 ]; then
  echo "推送失败（3 次）" >&2
  exit 1
fi

echo "[ok] lean-release = ${RELEASE_SHA}（设备构建、设备验证运行后发布）"
echo "    冒烟门禁: EXPECT_COMMIT=$RELEASE_SHA ./tools/release/smoke_gate.sh"
