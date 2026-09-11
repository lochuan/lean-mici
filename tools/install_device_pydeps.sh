#!/usr/bin/env bash
# Install the device's Python deps into /data/pydeps.
#
# Why /data/pydeps and not the venv: /usr/local/venv is on the read-only AGNOS
# rootfs and is replaced wholesale by AGNOS updates. launch_chffrplus.sh already
# puts /data/pydeps on PYTHONPATH, so anything here is importable by every
# openpilot process and survives AGNOS upgrades.
#
# aiohttp used to come from the AGNOS venv; 19.6 dropped it, which killed
# models_manager on device while CI stayed green. Deps that are not part of
# openpilot's own tree belong here, pinned, not assumed present.
#
# CAUTION: /data/pydeps precedes site-packages on sys.path, so a package
# installed here SHADOWS the venv's copy for every process. Keep this set
# minimal and never install something the venv already provides -- that is why
# setuptools is pruned below (sanic wants >=70.1.0; the venv has 83.0.0).
#
# Usage: ./tools/install_device_pydeps.sh [user@host]
set -euo pipefail

HOST="${1:-comma@10.223.134.33}"
TARGET=/data/pydeps
PY=/usr/local/venv/bin/python

# Pinned so a reinstall cannot silently pull a different tree. All of these
# publish cp312 aarch64 wheels, so the device needs no compiler.
PKGS=(
  "sanic==25.12.1"
  "sanic-routing==23.12.0"
  "aiofiles==25.1.0"
  "html5tagger==2.0.0"
  "httptools==0.8.0"
  "multidict==6.8.0"
  "tracerite==2.6.5"
  "typing_extensions==4.16.0"
  "ujson==6.0.0"
  "uvloop==0.22.1"
  "websockets==17.1"
)

echo "[-] installing ${#PKGS[@]} package(s) into ${HOST}:${TARGET}"
ssh "$HOST" "mkdir -p '$TARGET' && '$PY' -m pip install --target '$TARGET' \
  --no-compile --upgrade --only-binary=:all: ${PKGS[*]}" >/dev/null

# pip drags setuptools in as a sanic dependency; installing it here would
# shadow the venv's for every process. The venv's version already satisfies it.
echo "[-] pruning setuptools/distutils shims so they cannot shadow the venv"
ssh "$HOST" "cd '$TARGET' && rm -rf setuptools setuptools-*.dist-info \
  pkg_resources _distutils_hack distutils-precedence.pth"

echo "[-] verifying"
ssh "$HOST" "cd /data/openpilot && PYTHONPATH=/data/openpilot:'$TARGET' '$PY' - <<'PY'
import sys, sanic, setuptools
assert sanic.__file__.startswith('/data/pydeps'), sanic.__file__
assert 'site-packages' in setuptools.__file__, 'pydeps is shadowing venv setuptools'
sp = next(i for i, p in enumerate(sys.path) if 'site-packages' in p)
pd = next(i for i, p in enumerate(sys.path) if p == '/data/pydeps')
assert pd < sp, 'pydeps must precede site-packages'
print(f'  sanic {sanic.__version__} from /data/pydeps; venv setuptools {setuptools.__version__} intact')
PY"

# Anything in the venv would be shadowed by the same-named package here.
echo "[-] checking for packages that shadow the venv"
ssh "$HOST" "SP=/usr/local/venv/lib/python3.12/site-packages; \
  cd '$TARGET' && for d in \$(ls | grep -vE 'dist-info|__pycache__'); do \
    [ -e \"\$SP/\$d\" ] && echo \"     SHADOWS VENV: \$d\"; done; true"

echo "[ok] $(ssh "$HOST" "du -sh '$TARGET' | cut -f1") installed at ${TARGET}"
