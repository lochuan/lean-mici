#!/usr/bin/env bash
# 烧写本地构建的 sunnypilot system 镜像（19.7.2 + 蓝牙 overlay）到设备的 inactive slot。
#
# 只动 system 分区：kernel/boot/xbl 全部沿用我们 OTA mirror 的 19.7.2 产物，
# 与设备现行 slot 一致，不需要 boot.img。流程仿 StarPilot 的
# tools/agnos/flash_local_agnos_pair_to_comma.sh，但裁掉 boot：
#   1. 本机校验 manifest（单 system 条目、A/B、sparse raw payload）
#   2. scp manifest + system.img.xz + agnos.py 到设备 /data/local_system_flash/
#   3. 设备侧起本地 HTTP（127.0.0.1:8989）-> agnos.py --swap 写入 inactive slot
#   4. tmux 里跑，断开也继续；最后 sudo reboot
#
# 用法：
#   tools/lanlink/flash_local_system_to_comma.sh [host] [system.img.xz]
#   HOST 也可以用环境变量 SSH_HOST 覆盖；镜像默认用 agnos-builder 的 output 产物。
#
# 烧写前设备侧早验（见 design §8 R2）：
#   ls /dev/disk/by-partlabel/ | grep bluetooth   # QCA 固件分区
#   ls /dev/ttyHS1 /dev/btpower                    # 蓝牙串口与电源节点
set -euo pipefail

HOST="${1:-${SSH_HOST:-comma@192.168.3.110}}"
SYSTEM_IMAGE="${2:-$HOME/Documents/Projects/agnos-builder/output/system-19.7.3-bt.img.xz}"
EXPECTED_VERSION="${EXPECTED_VERSION:-19.7.2}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST="$REPO_ROOT/openpilot/common/hardware/comma/agnos.json"

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
if [[ -n "${SSH_KEY:-}" ]]; then
  SSH_OPTS=(-i "$SSH_KEY" "${SSH_OPTS[@]}")
fi

for path in "$MANIFEST" "$SYSTEM_IMAGE"; do
  if [[ ! -f "$path" ]]; then
    echo "missing file: $path" >&2
    exit 1
  fi
done

SESSION="local_system_flash"
REMOTE_DIR="/data/local_system_flash"
REMOTE_MANIFEST="${REMOTE_DIR}/agnos-local.json"
REMOTE_RUNNER="${REMOTE_DIR}/run_flash.sh"
REMOTE_AGNOS="${REMOTE_DIR}/agnos.py"
PORT="8989"

LOCAL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agnos-system.XXXXXX")"
trap 'rm -rf "$LOCAL_DIR"' EXIT
LOCAL_MANIFEST="${LOCAL_DIR}/agnos-local.json"

# manifest 裁成单 system 条目 + url 指到设备本地 HTTP（agnos.py 只认 url 下载）
python3 - "$MANIFEST" "$LOCAL_MANIFEST" "$PORT" "$SYSTEM_IMAGE" <<'PY'
import json
import sys
from pathlib import Path

source, destination, port, system_image = sys.argv[1:]
manifest = json.loads(Path(source).read_text(encoding="utf-8"))
entries = [e for e in manifest if e.get("name") == "system"]
if len(entries) != 1:
  raise SystemExit("manifest must contain exactly one system entry")

entry = dict(entries[0])
for key in ("ondevice_hash", "alt"):
  entry.pop(key, None)
if not entry.get("has_ab"):
  raise SystemExit("system must be an A/B partition")
if entry.get("sparse"):
  raise SystemExit("system must use a raw, non-sparse payload for local flashing")
entry["url"] = f"http://127.0.0.1:{port}/{Path(system_image).name}"

Path(destination).write_text(json.dumps([entry], indent=2) + "\n", encoding="utf-8")
PY

ssh "${SSH_OPTS[@]}" "$HOST" "mkdir -p '$REMOTE_DIR'"
scp "${SSH_OPTS[@]}" "$LOCAL_MANIFEST" "$HOST:$REMOTE_MANIFEST"
scp "${SSH_OPTS[@]}" "$REPO_ROOT/openpilot/common/hardware/comma/agnos.py" "$HOST:$REMOTE_AGNOS"
scp "${SSH_OPTS[@]}" "$SYSTEM_IMAGE" "$HOST:$REMOTE_DIR/"

ssh "${SSH_OPTS[@]}" "$HOST" "cat > '$REMOTE_RUNNER' && chmod +x '$REMOTE_RUNNER'" <<'REMOTE_RUNNER'
#!/usr/bin/env bash
set -euo pipefail

: "${REMOTE_DIR:?}"
: "${REMOTE_MANIFEST:?}"
: "${REMOTE_AGNOS:?}"
: "${PORT:?}"
: "${EXPECTED_VERSION:?}"

exec > >(tee -a "${REMOTE_DIR}/flash.log") 2>&1

echo "[STEP] Local sunnypilot system flash (single partition)"
echo "[CHECK] Device: $(tr -d '\0' </sys/firmware/devicetree/base/model)"
echo "[CHECK] Installed AGNOS: $(cat /VERSION 2>/dev/null || echo unknown)"
echo "[CHECK] Target AGNOS: ${EXPECTED_VERSION}"
echo "[CHECK] Active slot before flash: $(abctl --boot_slot)"
df -h /data

if [[ -x /usr/local/venv/bin/python3 ]]; then
  PYTHON_BIN="/usr/local/venv/bin/python3"
else
  PYTHON_BIN="python3"
fi

# agnos.py 顶层 import requests：设备 venv 里有，系统 python3 不一定有
if ! "$PYTHON_BIN" -c "import requests" 2>/dev/null; then
  echo "[ERROR] $PYTHON_BIN has no requests module" >&2
  exit 1
fi

pkill -f "http.server ${PORT}.*${REMOTE_DIR}" >/dev/null 2>&1 || true
"$PYTHON_BIN" -m http.server "$PORT" --bind 127.0.0.1 --directory "$REMOTE_DIR" >"${REMOTE_DIR}/http.log" 2>&1 &
http_pid="$!"
trap 'kill "$http_pid" >/dev/null 2>&1 || true' EXIT

http_ready=0
for _ in $(seq 1 20); do
  if "$PYTHON_BIN" - "$REMOTE_MANIFEST" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

for entry in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")):
  with urllib.request.urlopen(entry["url"], timeout=2) as response:
    response.read(1)
PY
  then
    http_ready=1
    break
  fi
  sleep 0.25
done

if [[ "$http_ready" != "1" ]]; then
  echo "[ERROR] Local image HTTP server did not become ready" >&2
  cat "${REMOTE_DIR}/http.log" >&2 || true
  exit 1
fi

echo "[FLASH] Writing system to the inactive AGNOS slot"
"$PYTHON_BIN" "$REMOTE_AGNOS" --swap "$REMOTE_MANIFEST"

echo "[DONE] System verified and the inactive slot was selected"
echo "[REBOOT] Rebooting now"
sudo reboot
REMOTE_RUNNER

ssh "${SSH_OPTS[@]}" "$HOST" "tmux kill-session -t '$SESSION' >/dev/null 2>&1 || true"
ssh "${SSH_OPTS[@]}" "$HOST" "rm -f '$REMOTE_DIR/flash.log' '$REMOTE_DIR/http.log'"
ssh "${SSH_OPTS[@]}" "$HOST" \
  "tmux new-session -d -s '$SESSION' \"REMOTE_DIR='$REMOTE_DIR' REMOTE_MANIFEST='$REMOTE_MANIFEST' REMOTE_AGNOS='$REMOTE_AGNOS' PORT='$PORT' EXPECTED_VERSION='$EXPECTED_VERSION' bash '$REMOTE_RUNNER'\""

echo "Started remote tmux session: $SESSION"
echo "Watch it with: ssh $HOST 'tmux attach -t $SESSION'"
