#!/usr/bin/env bash
#
# publish_release_from_device.sh — 一键发布编排器（Mac 侧）。
#
# 固化流程（2026-09-25）：Mac 单测 → 辅助文件 → 设备侧 nohup 发布 → 轮询 →
# 中继三步（取 relstage / push fork / 设备消费）→ 重启 → 上机验证。
# 发布从此就是这一条命令（纯 Python 改动约 3 分钟；换 pin/换模型才走全量重编）。
#
# 用法: tools/release/publish_release_from_device.sh [--skip-tests]
# 环境: DEVICE=comma@10.0.0.27 可覆盖
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
DEVICE="${DEVICE:-comma@10.0.0.27}"
SKIP_TESTS=0
[ "${1:-}" = "--skip-tests" ] && SKIP_TESTS=1

echo "[-] Mac 侧单测（--skip-tests 跳过）"
if [ "$SKIP_TESTS" -eq 0 ]; then
  (cd "$ROOT" && uv run --extra testing --with pytest --with opencv-python-headless pytest -q \
    openpilot/selfdrive/eagled/tests \
    openpilot/selfdrive/controls/tests/test_avoidance_fusion.py \
    openpilot/selfdrive/controls/tests/test_desire_helper.py \
    openpilot/system/lanlinkd/tests/test_lanes.py \
    openpilot/system/lanlinkd/tests/test_avoidanced.py \
    tools/release/test_release_lib.py)
fi

echo "[-] 推送发布辅助文件（/tmp 随设备重启被清——先建目录，勿省）"
ssh "$DEVICE" 'mkdir -p /tmp/relhelper'
scp -q "$DIR/smoke_onroad_device.py" "$DIR/release_lib.py" "$DEVICE:/tmp/relhelper/"
scp -q "$DIR/device_release.sh" "$DEVICE:/tmp/device_release.sh"

echo "[-] 设备侧发布（nohup + 轮询；含 scons/键表门禁/schema 门禁/冒烟/打包）"
# 清树：上一次发布/调试留下的同步状态会让前提检查（树必须干净）误判。
# reset 绝不接管道（SIGPIPE 会掐死 reset，见 ADR 发布基建三连坑）。
ssh "$DEVICE" 'cd /data/openpilot && git reset -q --hard HEAD 2>/dev/null; (setsid nohup bash /tmp/device_release.sh > /tmp/release.log 2>&1 & echo $! > /tmp/release.pid)'
while ssh -o ConnectTimeout=10 "$DEVICE" 'kill -0 "$(cat /tmp/release.pid)" 2>/dev/null'; do
  sleep 20
done
if ! ssh "$DEVICE" 'grep -q "扁平树 stage 就绪" /tmp/release.log'; then
  ssh "$DEVICE" 'tail -30 /tmp/release.log' >&2 || true
  echo "设备端发布失败（见上方日志）" >&2
  exit 1
fi

echo "[-] 中继三步：取 relstage → push fork → 设备消费"
git fetch "ssh://$DEVICE/data/relstage" +lean-release:refs/temp/device-release
RELEASE_SHA=$(git rev-parse refs/temp/device-release)
PUSHED=0
for _ in 1 2 3; do
  if git push -f fork refs/temp/device-release:lean-release; then
    PUSHED=1
    break
  fi
  sleep 5
done
[ "$PUSHED" -eq 1 ] || { echo "推送 fork 失败（3 次）" >&2; exit 1; }
ssh "$DEVICE" 'cd /data/openpilot && git fetch -q origin lean-release && git reset -q --hard FETCH_HEAD'

echo "[-] 重启 + 上机验证"
ssh "$DEVICE" 'sudo systemctl restart comma'
sleep 40
ssh "$DEVICE" "cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -W ignore -c '
from openpilot.common.params import Params
Params().check_key(\"AvoidanceSideMargin\")
print(\"[ok] params 键抽查\")
from openpilot.cereal import services
assert services.SERVICE_LIST[\"eagleDebug\"].should_log is True
print(\"[ok] eagleDebug 落盘开关\")
'"

echo "[ok] lean-release = ${RELEASE_SHA} 已部署（设备重启完成）"
echo "    冒烟门禁: EXPECT_COMMIT=$RELEASE_SHA ./tools/release/smoke_gate.sh"
