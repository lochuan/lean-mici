#!/usr/bin/env bash
#
# publish_release_from_device.sh — 一键发布编排器（Mac 侧）。
#
# 流程：Mac 单测 → scp device_release.sh → 设备侧 nohup 构建（/data/build 干净构建树 +
# scons 全量，未变部分走 /data/scons_cache）→ 轮询 → 中继三步（取 relstage / push fork
# （lean-release 再镜像到 gitee remote）/ 设备消费）→ 重启 → 上机验证。
#
# 用法: tools/release/publish_release_from_device.sh [--skip-tests] [--skip-smoke] [--local-source]
#       --local-source：本地 nav-test → nav-local-release，必须指定 EXPECTED_SOURCE_SHA，不推 fork/gitee。
#       末尾默认跑台架冒烟 tools/bench/smoke_after_build.sh（jungle 点火回放，验证核心进程 onroad 稳定）；
#       没接 jungle 时自动跳过，--skip-smoke 强制跳过。
# 环境: DEVICE=comma@10.0.0.27 可覆盖
#       SRC_BRANCH / RELEASE_BRANCH 透传给设备侧 device_release.sh
#       （缺省 lean-master / lean-release；如 SRC_BRANCH=big-uplink RELEASE_BRANCH=big-release）
#       ADB_SERIAL=55873f9 走 adb 隧道（DEVICE=c4usb → localhost:2222）时设上，重启后自动重建转发
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
DEVICE="${DEVICE:-comma@10.0.0.27}"
SRC_BRANCH="${SRC_BRANCH:-lean-master}"
RELEASE_BRANCH="${RELEASE_BRANCH:-lean-release}"
SKIP_TESTS=0
SKIP_SMOKE=0
LOCAL_SOURCE=0
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    --skip-smoke) SKIP_SMOKE=1 ;;
    --local-source) LOCAL_SOURCE=1 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done
if [ "$LOCAL_SOURCE" -eq 1 ]; then
  SRC_BRANCH=nav-test
  RELEASE_BRANCH=nav-local-release
  [[ ${EXPECTED_SOURCE_SHA:-} =~ ^[0-9a-f]{40}$ ]] || { echo "必须传入 EXPECTED_SOURCE_SHA（40 位小写十六进制）" >&2; exit 1; }
  LOCAL_WORKER=/data/build/.git/local-release.py
  LOCAL_PREPARE_STARTED=0
  local_failure() {
    local rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "本地发版失败。" >&2
      if [ "$LOCAL_PREPARE_STARTED" -eq 1 ]; then
        echo "先查看设备状态和 /tmp/consume.log 或 /tmp/release.log；等任务结束后再回滚。" >&2
        printf "回滚命令：ssh %q '(setsid nohup /usr/local/venv/bin/python /data/build/.git/local-release.py rollback > /tmp/rollback.log 2>&1 < /dev/null &)'\n" "$DEVICE" >&2
      else
        echo "本次未调用 prepare，未进入参数设置/设备构建；检查上述错误后重试。" >&2
      fi
    fi
  }
  trap local_failure EXIT
  trap 'exit 1' HUP INT TERM
fi

echo "[-] Mac 侧单测（--skip-tests 跳过）"
if [ "$SKIP_TESTS" -eq 0 ]; then
  (cd "$ROOT" && uv run --extra testing --with pytest --with opencv-python-headless pytest -q \
    openpilot/selfdrive/controls/tests/test_desire_helper.py \
    tools/release/test_release_lib.py)
fi

if [ "$LOCAL_SOURCE" -eq 1 ]; then
  if [ "$SKIP_TESTS" -eq 0 ]; then
    (cd "$ROOT" && uv run --extra testing --with pytest pytest -q \
      tools/release/test_local_release.py tools/release/test_local_release_device.py)
  fi
  echo "[-] 从 Mac 传入本地 nav-test 与固定子模块（不访问公共远端）"
  LOCAL_SOURCE_SHA=$(python3 "$DIR/release_lib.py" send-local-source "$ROOT" "$DEVICE" "$EXPECTED_SOURCE_SHA")
  if ! [[ $LOCAL_SOURCE_SHA =~ ^[0-9a-f]{40}$ ]] || [ "$LOCAL_SOURCE_SHA" != "$EXPECTED_SOURCE_SHA" ]; then
    echo "本地源 stdout/SHA 校验失败" >&2
    exit 1
  fi
  scp -q "$DIR/local_release.py" "$DEVICE:$LOCAL_WORKER"
  LOCAL_PREPARE_STARTED=1
  # shellcheck disable=SC2029 # 固定设备路径，SHA 已严格校验；参数应在 Mac 展开。
  ssh "$DEVICE" "/usr/local/venv/bin/python $LOCAL_WORKER prepare $LOCAL_SOURCE_SHA"
fi

echo "[-] 推送设备侧发布脚本（构建树 /data/build 由它 checkout，release_lib.py 从构建树调用）"
scp -q "$DIR/device_release.sh" "$DEVICE:/tmp/device_release.sh"

echo "[-] 设备侧发布（nohup + 轮询；checkout 构建树 → scons 全量构建 → 剥离 → 打包）"
if [ "$LOCAL_SOURCE" -eq 1 ]; then
  ssh "$DEVICE" '(setsid nohup env SRC_BRANCH='"$SRC_BRANCH"' RELEASE_BRANCH='"$RELEASE_BRANCH"' LOCAL_SOURCE_SHA='"$LOCAL_SOURCE_SHA"' bash /tmp/device_release.sh > /tmp/release.log 2>&1 & echo $! > /tmp/release.pid)'
else
ssh "$DEVICE" '(setsid nohup env SRC_BRANCH='"$SRC_BRANCH"' RELEASE_BRANCH='"$RELEASE_BRANCH"' bash /tmp/device_release.sh > /tmp/release.log 2>&1 & echo $! > /tmp/release.pid)'
fi
while ssh -o ConnectTimeout=10 "$DEVICE" 'kill -0 "$(cat /tmp/release.pid)" 2>/dev/null'; do
  sleep 20
done
if ! ssh "$DEVICE" 'grep -q "扁平树 stage 就绪" /tmp/release.log'; then
  ssh "$DEVICE" 'tail -30 /tmp/release.log' >&2 || true
  echo "设备端发布失败（见上方日志）" >&2
  exit 1
fi

if [ "$LOCAL_SOURCE" -eq 1 ]; then
  GITEE_WARNING=""
  echo "[-] 车机直接消费 /data/relstage（不推 fork/gitee，保留 lean-release 回滚分支）"
  RELEASE_SHA=$(ssh "$DEVICE" 'git -C /data/relstage rev-parse refs/heads/nav-local-release')
  [[ $RELEASE_SHA =~ ^[0-9a-f]{40}$ ]] || { echo "release SHA 格式错误" >&2; exit 1; }
  # shellcheck disable=SC2029 # 固定设备路径，两个 SHA 已严格校验；参数应在 Mac 展开。
  ssh "$DEVICE" "(setsid nohup /usr/local/venv/bin/python $LOCAL_WORKER consume $LOCAL_SOURCE_SHA $RELEASE_SHA > /tmp/consume.log 2>&1 < /dev/null &)"
  phase=""
  for _ in $(seq 72); do
    phase=$(ssh -o ConnectTimeout=5 "$DEVICE" "/usr/local/venv/bin/python $LOCAL_WORKER status" 2>/dev/null) || phase=""
    case "$phase" in
      rebooting) break ;;
      failed|recovered|rolled-back) ssh "$DEVICE" 'tail -40 /tmp/consume.log' >&2 || true; exit 1 ;;
    esac
    sleep 5
  done
  [ "$phase" = rebooting ] || { echo "消费状态未确认，勿另行重启；先查看设备日志/状态" >&2; exit 1; }
else
echo "[-] 中继三步：取 relstage → push fork → 设备消费"
git fetch "ssh://$DEVICE/data/relstage" +"$RELEASE_BRANCH":refs/temp/device-release
RELEASE_SHA=$(git rev-parse refs/temp/device-release)
# 用法: push_release_to_remote <remote>；最多试 3 次，成功返回 0
push_release_to_remote() {
  for _ in 1 2 3; do
    # 目标必须全限定：新分支（如 big-release）远端不存在时 git 拒绝 DWIM 猜名
    git push -f "$1" refs/temp/device-release:"refs/heads/$RELEASE_BRANCH" && return 0
    sleep 5
  done
  return 1
}
push_release_to_remote fork || { echo "推送 fork 失败（3 次）" >&2; exit 1; }
# 国内镜像（installer 的 /c4cn 装 gitee，车机 OTA 也走它）：只镜像 lean-release。
# 失败不中断发版（fork 已推、设备照常消费），只告警并给补推命令。
GITEE_WARNING=""
if [ "$RELEASE_BRANCH" = "lean-release" ]; then
  if git remote get-url gitee >/dev/null 2>&1; then
    push_release_to_remote gitee || GITEE_WARNING="推送 gitee 失败（3 次），国内用户仍是旧版。补推: git push -f gitee $RELEASE_SHA:refs/heads/$RELEASE_BRANCH"
  else
    GITEE_WARNING="没有 gitee remote，未镜像。先 git remote add gitee git@gitee.com:lochuan/toyo-pilot.git，再 git push -f gitee $RELEASE_SHA:refs/heads/$RELEASE_BRANCH"
  fi
  [ -z "$GITEE_WARNING" ] || echo "[!] $GITEE_WARNING" >&2
fi
ssh "$DEVICE" "cd /data/openpilot && git fetch -4 -q origin $RELEASE_BRANCH && git checkout -q --force -B $RELEASE_BRANCH FETCH_HEAD"

fi

echo "[-] 重启 + 上机验证"
# 必须整机重启：systemctl restart comma 不清内核的 touch_count，开机累计 >4 次触摸时
# comma.sh 会进 tap-reset 界面，openpilot 起不来（2026-10-03 踩过）。
if [ "$LOCAL_SOURCE" -eq 0 ]; then
ssh "$DEVICE" 'sudo reboot' || true
fi
sleep 30
for _ in $(seq 36); do
  [ -n "${ADB_SERIAL:-}" ] && adb -s "$ADB_SERIAL" forward tcp:2222 tcp:22 >/dev/null 2>&1 || true
  ssh -o ConnectTimeout=5 "$DEVICE" true 2>/dev/null && break
  sleep 5
done
sleep 30
ssh "$DEVICE" 'pgrep -f "[m]anager.py" >/dev/null && ! pgrep -f "/usr/comma/[r]eset" >/dev/null' \
  || { echo "[x] 重启后 manager 未运行（或卡在 reset 界面）"; exit 1; }
echo "[ok] manager 运行中"
ssh "$DEVICE" "cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python -W ignore -c '
from openpilot.common.params import Params
Params().check_key(\"BluetoothEnabled\")
print(\"[ok] params 键抽查\")
'"

if [ "$LOCAL_SOURCE" -eq 1 ]; then
  # shellcheck disable=SC2029 # 仅预期 SHA 在 Mac 展开，HEAD 和参数值在设备读取。
  ssh "$DEVICE" "cd /data/openpilot && test \"\$(git rev-parse HEAD)\" = $RELEASE_SHA && git diff --quiet HEAD -- && test \"\$(cat /data/params/d/DisableUpdates)\" = 1"
fi

echo "[ok] $RELEASE_BRANCH = ${RELEASE_SHA} 已部署（设备重启完成）"
[ -z "$GITEE_WARNING" ] || echo "[!] $GITEE_WARNING" >&2

if [ "$SKIP_SMOKE" -eq 0 ]; then
  echo "[-] 台架冒烟（jungle 点火回放）"
  rc=0
  DEVICE="$DEVICE" "$ROOT/tools/bench/smoke_after_build.sh" || rc=$?
  case "$rc" in
    0) ;;
    3) echo "[skip] 没接 jungle，跳过冒烟" ;;
    *) echo "[x] 台架冒烟失败：$RELEASE_BRANCH 已部署但核心进程不稳定，见上方输出" >&2; exit 1 ;;
  esac
fi
