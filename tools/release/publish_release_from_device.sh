#!/usr/bin/env bash
#
# publish_release_from_device.sh — 一键发布编排器（Mac 侧）。
#
# 流程：Mac 单测 → R2 DBC 检查 → scp device_release.sh → 设备侧 nohup 构建
# （/data/build 干净构建树 + scons 全量，未变部分走 /data/scons_cache）→ 轮询 →
# 按需导入/备份 DBC 密钥 → push fork/Gitee → 上传 R2 并清理旧版 → 设备消费 → 重启验证。
#
# 用法: tools/release/publish_release_from_device.sh [--skip-tests] [--skip-smoke] [--local-source]
#       始终使用 LOCAL_SOURCE_REF + EXPECTED_SOURCE_SHA + PYDEPS_ARTIFACT 的固定本地输入。
#       --local-source：默认 refs/heads/nav-test → nav-local-release，不推 fork/gitee。
#       末尾默认跑台架冒烟 tools/bench/smoke_after_build.sh（jungle 点火回放，验证核心进程 onroad 稳定）；
#       没接 jungle 时自动跳过，--skip-smoke 强制跳过。
# 环境: DEVICE=comma@10.0.0.27 可覆盖
#       SRC_BRANCH / RELEASE_BRANCH 透传给设备侧 device_release.sh
#       （缺省 lean-master / lean-release；如 SRC_BRANCH=big-uplink RELEASE_BRANCH=big-release）
#       ADB_SERIAL=55873f9 走 adb 隧道（DEVICE=c4usb → localhost:2222）时设上，重启后自动重建转发
#       正式 lean-release 的 DBC 检查需要 DOWNLOAD_BASE_URL；需要重建时还需要 R2 凭据、
#       DEPLOY_SSH_ALIAS / DEPLOY_ROOT / AGE_RECIPIENT，TOYOCHIP_SERVER_ROOT 可覆盖相邻仓库路径。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
DEVICE="${DEVICE:-comma@10.0.0.27}"
SRC_BRANCH="${SRC_BRANCH:-lean-master}"
RELEASE_BRANCH="${RELEASE_BRANCH:-lean-release}"
TOYOCHIP_SERVER_ROOT="${TOYOCHIP_SERVER_ROOT:-$ROOT/../toyochip-server}"
DBC_BUILD=0
DBC_TEMP=""
cleanup_dbc_temp() {
  if [ -n "$DBC_TEMP" ]; then
    rm -rf -- "$DBC_TEMP"
    DBC_TEMP=""
  fi
}
trap cleanup_dbc_temp EXIT
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
  LOCAL_SOURCE_REF="${LOCAL_SOURCE_REF:-refs/heads/nav-test}"
  RELEASE_BRANCH=nav-local-release
else
  LOCAL_SOURCE_REF="${LOCAL_SOURCE_REF:-refs/heads/$SRC_BRANCH}"
fi
[[ $LOCAL_SOURCE_REF =~ ^refs/heads/[A-Za-z0-9_][A-Za-z0-9_./-]*$ ]] || { echo "LOCAL_SOURCE_REF 必须为完整本地分支 ref" >&2; exit 1; }
[[ $RELEASE_BRANCH =~ ^[A-Za-z0-9_][A-Za-z0-9_./-]*$ ]] || { echo "RELEASE_BRANCH 格式无效" >&2; exit 1; }
SRC_BRANCH="${LOCAL_SOURCE_REF#refs/heads/}"
[[ ${EXPECTED_SOURCE_SHA:-} =~ ^[0-9a-f]{40}$ ]] || { echo "必须传入 EXPECTED_SOURCE_SHA（40 位小写十六进制）" >&2; exit 1; }
[ -d "${PYDEPS_ARTIFACT:-}" ] || { echo "必须指定已验证离线产物目录 PYDEPS_ARTIFACT" >&2; exit 1; }
git check-ref-format "$LOCAL_SOURCE_REF" || exit 1
git check-ref-format "refs/heads/$RELEASE_BRANCH" || exit 1
if [ "$LOCAL_SOURCE" -eq 0 ] && [ "$SRC_BRANCH" = "lean-master" ] && [ "$RELEASE_BRANCH" = "lean-release" ]; then
  [ -n "${DOWNLOAD_BASE_URL:-}" ] || { echo "必须设置 DOWNLOAD_BASE_URL 以检查 R2 DBC" >&2; exit 1; }
  DBC_BUILD=$(python3 "$DIR/release_lib.py" dbc-needs-build "$DOWNLOAD_BASE_URL" "$EXPECTED_SOURCE_SHA")
  [[ $DBC_BUILD =~ ^[01]$ ]] || { echo "DBC 更新检查返回无效结果" >&2; exit 1; }
  if [ "$DBC_BUILD" = "0" ] && ssh "$DEVICE" "test -f /data/build/.git/dbc-release/$EXPECTED_SOURCE_SHA/public/manifest.json && test -f /data/build/.git/dbc-release/$EXPECTED_SOURCE_SHA/private/key.json"; then
    DBC_BUILD=2
  fi
  if [ "$DBC_BUILD" != "0" ]; then
    [ -n "${DEPLOY_SSH_ALIAS:-}" ] && [ -n "${DEPLOY_ROOT:-}" ] && [ -n "${AGE_RECIPIENT:-}" ] \
      || { echo "DBC 更新需要 DEPLOY_SSH_ALIAS、DEPLOY_ROOT、AGE_RECIPIENT" >&2; exit 1; }
    for helper in release/r2_release.py server/activation/map_key_backup.py server/activation/map_key_restore.py; do
      [ -f "$TOYOCHIP_SERVER_ROOT/$helper" ] || { echo "缺少 toyochip 发布工具: $TOYOCHIP_SERVER_ROOT/$helper" >&2; exit 1; }
    done
    [ "$DBC_BUILD" = "1" ] && echo "[-] R2 为空或源提交/opendbc/DBC 已更新，将自动构建并发布 DBC"
    [ "$DBC_BUILD" = "1" ] || echo "[-] 复用设备上未完成的 DBC 包，继续发布"
  else
    echo "[-] R2 已有当前源提交的 DBC，跳过重建"
  fi
fi
if [ "$LOCAL_SOURCE" -eq 1 ]; then
  LOCAL_WORKER=/data/build/.git/local-release.py
  LOCAL_PREPARE_STARTED=0
  local_failure() {
    local rc=$?
    if [ "$rc" -ne 0 ]; then
      echo "本地发版失败。" >&2
      if [ "$LOCAL_PREPARE_STARTED" -eq 1 ]; then
        echo "先查看设备状态和 /tmp/consume.log 或 /tmp/release.log；等任务结束后再回滚。" >&2
        printf "确认 state 属于本次源版本后，回滚命令：ssh %q '(setsid nohup /usr/local/venv/bin/python /data/build/.git/local-release.py rollback %s > /tmp/rollback.log 2>&1 < /dev/null &)'\n" "$DEVICE" "$EXPECTED_SOURCE_SHA" >&2
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
    tools/release)
fi

echo "[-] 从 Mac 传入本地 $LOCAL_SOURCE_REF 与固定子模块（不访问公共远端）"
LOCAL_SOURCE_SHA=$(python3 "$DIR/release_lib.py" send-local-source "$ROOT" "$DEVICE" "$EXPECTED_SOURCE_SHA" --source-ref "$LOCAL_SOURCE_REF")
if ! [[ $LOCAL_SOURCE_SHA =~ ^[0-9a-f]{40}$ ]] || [ "$LOCAL_SOURCE_SHA" != "$EXPECTED_SOURCE_SHA" ]; then
  echo "本地源 stdout/SHA 校验失败" >&2
  exit 1
fi
python3 "$DIR/release_lib.py" send-local-pydeps "$ROOT" "$DEVICE" "$LOCAL_SOURCE_SHA" "$PYDEPS_ARTIFACT"
scp -q "$ROOT/openpilot/common/pydeps.py" "$DEVICE:/data/build/.git/pydeps.py"
if [ "$LOCAL_SOURCE" -eq 1 ]; then
  scp -q "$DIR/local_release.py" "$DEVICE:$LOCAL_WORKER"
  LOCAL_PREPARE_STARTED=1
  # shellcheck disable=SC2029 # 固定设备路径，SHA 已严格校验；参数应在 Mac 展开。
  ssh "$DEVICE" "/usr/local/venv/bin/python $LOCAL_WORKER prepare $LOCAL_SOURCE_SHA --source-ref $LOCAL_SOURCE_REF"
else
  ssh "$DEVICE" "/usr/local/venv/bin/python -I -B /data/build/.git/pydeps.py install /data/build/.git/pydeps/$LOCAL_SOURCE_SHA"
fi

echo "[-] 推送设备侧发布脚本（构建树 /data/build 由它 checkout，release_lib.py 从构建树调用）"
scp -q "$DIR/device_release.sh" "$DEVICE:/tmp/device_release.sh"

echo "[-] 设备侧发布（nohup + 轮询；checkout 构建树 → scons 全量构建 → 剥离 → 打包）"
ssh "$DEVICE" '(setsid nohup env SRC_BRANCH='"$SRC_BRANCH"' RELEASE_BRANCH='"$RELEASE_BRANCH"' LOCAL_SOURCE_SHA='"$LOCAL_SOURCE_SHA"' DBC_BUILD='"$DBC_BUILD"' bash /tmp/device_release.sh > /tmp/release.log 2>&1 & echo $! > /tmp/release.pid)'
while ssh -o ConnectTimeout=10 "$DEVICE" 'kill -0 "$(cat /tmp/release.pid)" 2>/dev/null'; do
  sleep 20
done
if ! ssh "$DEVICE" 'grep -q "扁平树 stage 就绪" /tmp/release.log'; then
  ssh "$DEVICE" 'tail -30 /tmp/release.log' >&2 || true
  echo "设备端发布失败（见上方日志）" >&2
  exit 1
fi

if [ "$DBC_BUILD" != "0" ]; then
  echo "[-] 取回 DBC 公共包与独立私钥记录，导入并验证密钥备份"
  DBC_TEMP=$(mktemp -d /tmp/lean-dbc-release.XXXXXX)
  mkdir "$DBC_TEMP/public"
  scp -q "$DEVICE:/data/build/.git/dbc-release/$LOCAL_SOURCE_SHA/public/manifest.json" \
    "$DEVICE:/data/build/.git/dbc-release/$LOCAL_SOURCE_SHA/public/package.enc" "$DBC_TEMP/public/"
  scp -q "$DEVICE:/data/build/.git/dbc-release/$LOCAL_SOURCE_SHA/private/key.json" "$DBC_TEMP/key.json"
  printf -v remote_map_keys '%q' "$DEPLOY_ROOT/server/activation/map_keys.py"
  ssh "$DEPLOY_SSH_ALIAS" "sudo -n python3 $remote_map_keys --store /var/lib/chipmunk-map-keys import" < "$DBC_TEMP/key.json"
  backup_receipt=$(python3 "$TOYOCHIP_SERVER_ROOT/server/activation/map_key_backup.py" \
    --ssh-target "$DEPLOY_SSH_ALIAS" --remote-cli "$DEPLOY_ROOT/server/activation/map_keys.py" --recipient "$AGE_RECIPIENT")
  backup_archive=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["backup_file"])' <<< "$backup_receipt")
  python3 "$TOYOCHIP_SERVER_ROOT/server/activation/map_key_restore.py" \
    --archive "$backup_archive" --expected-record-stdin < "$DBC_TEMP/key.json"
  echo "[-] DBC 密钥门通过，等待 release 推送后上传"
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
if [ "$DBC_BUILD" != "0" ]; then
  echo "[-] release 已推送，上传 DBC 到 R2 并清理旧版本"
  python3 "$TOYOCHIP_SERVER_ROOT/release/r2_release.py" dbc "$DBC_TEMP/public"
  ssh "$DEVICE" "rm -rf /data/build/.git/dbc-release/$LOCAL_SOURCE_SHA" \
    || echo "[!] DBC 已发布，但设备临时私钥目录清理失败，请手动清理" >&2
  cleanup_dbc_temp
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

ssh "$DEVICE" 'cd /data/openpilot && /usr/local/venv/bin/python -I -B openpilot/common/pydeps.py path /data/openpilot && pgrep -f "[l]anlinkd" >/dev/null && curl --fail --max-time 10 http://127.0.0.1:8088/api/status >/dev/null'
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
