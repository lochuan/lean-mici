#!/usr/bin/env bash
# homelab 同步：GitHub 为主，homelab 只是内网 OTA 的影子。
# 用法：tools/release/push_to_homelab.sh [homelab-host]
# 同步 sunnypilot 全部分支 + agnos system 镜像（有变化才传）。
set -euo pipefail
HOMELAB="${1:-lochuan@10.0.0.5}"
OTA=~/agnos-ota
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

echo "==> git mirror sync (homelab pulls from GitHub, retry push seed if behind)"
git -C "$REPO_ROOT" push --all fork 2>/dev/null || echo "note: fork push skipped/failed"
ssh "$HOMELAB" 'docker exec agnos-ota-git git --git-dir=/mirror/sunnypilot.git remote update --prune || true'

echo "==> agnos images"
for img in "$HOME/Documents/Projects/agnos-builder/output"/system-*.img.xz; do
  [ -f "$img" ] || continue
  name="$(basename "$img")"
  remote_sha="$(ssh "$HOMELAB" "sha256sum $OTA/www/$name 2>/dev/null" | cut -d" " -f1 || true)"
  local_sha="$(sha256sum "$img" | cut -d" " -f1)"
  if [ "$remote_sha" = "$local_sha" ]; then
    echo "skip $name (identical)"
  else
    echo "upload $name"
    scp "$img" "$HOMELAB:$OTA/www/"
    ssh "$HOMELAB" "cd $OTA/www && sha256sum $name | tee $name.sha256"
  fi
done
echo done
