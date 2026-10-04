#!/usr/bin/env bash
#
# 构建/发布后的台架冒烟：jungle 点火 + 回放 CAN，验证设备进 onroad 后核心进程稳定不重启。
# 抓的是 "sunnypilot Unavailable" 这一类：card 等进程启动即崩、被 manager 反复拉起。
#
# 用法: tools/bench/smoke_after_build.sh        （发布重启后设备在线即可）
# 退出码: 0 通过；1 失败；3 没接 jungle（调用方按跳过处理）
# 环境: DEVICE=comma@10.0.0.27 可覆盖；SETTLE=45 onroad 后等待秒数；WATCH=20 稳定性观察秒数
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
DEVICE="${DEVICE:-comma@10.0.0.27}"
SETTLE="${SETTLE:-45}"
WATCH="${WATCH:-20}"
PROCS="selfdrive.car.card selfdrive.controls.controlsd selfdrive.selfdrived.selfdrived selfdrive.modeld.modeld selfdrive.controls.plannerd selfdrive.controls.radard"
PY="$ROOT/.venv/bin/python"
REPLAY_PID=""

stop() {
  [ -n "$REPLAY_PID" ] && kill "$REPLAY_PID" 2>/dev/null && wait "$REPLAY_PID" 2>/dev/null
  "$PY" "$ROOT/tools/bench/jungle_replay.py" --off >/dev/null 2>&1
}
trap stop EXIT
fail() { echo "FAIL: $*"; exit 1; }
remote() { ssh -o ConnectTimeout=5 "$DEVICE" "$@"; }
pids() { remote "ps -eo pid,cmd | grep -F 'openpilot.$1' | grep -v grep | awk '{print \$1}' | head -1"; }

remote true || fail "设备连不上: $DEVICE"
"$PY" "$ROOT/tools/bench/jungle_replay.py" > /tmp/jungle_replay.log 2>&1 &
REPLAY_PID=$!

echo "[-] 等设备进 onroad"
for _ in $(seq 60); do
  [ "$(remote 'cat /data/params/d/IsOffroad' 2>/dev/null)" = "0" ] && break
  kill -0 "$REPLAY_PID" 2>/dev/null || {
    grep -q "没找到 jungle" /tmp/jungle_replay.log && { REPLAY_PID=""; exit 3; }
    fail "回放进程退出: $(tail -2 /tmp/jungle_replay.log)"
  }
  sleep 2
done
[ "$(remote 'cat /data/params/d/IsOffroad' 2>/dev/null)" = "0" ] || fail "120 秒内没进 onroad"

echo "[-] 等 ${SETTLE}s 让进程起来"
sleep "$SETTLE"
snapshot() { for p in $PROCS; do echo "$p $(pids "$p")"; done; }
before="$(snapshot)"
echo "$before"
echo "$before" | awk 'NF < 2 { print $1 " 没在跑"; bad = 1 } END { exit bad }' || fail "有核心进程没在跑"

echo "[-] 观察 ${WATCH}s，pid 不得变化（变了 = 被 manager 反复拉起）"
sleep "$WATCH"
[ "$(snapshot)" = "$before" ] || fail "核心进程 pid 变了（被重启过）"
echo "PASS: $PROCS 稳定运行 $((SETTLE + WATCH))s"
