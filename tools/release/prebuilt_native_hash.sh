#!/usr/bin/env bash
# prebuilt_native_hash.sh <commit> — native build-input tree hash.
# Kept as a thin wrapper so existing callers keep working; the real logic
# lives in release_lib.py and is shared with harvest and release validation.
set -e
commit="${1:-HEAD}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
python3 "$DIR/release_lib.py" hash "$commit"
