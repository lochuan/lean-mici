#!/usr/bin/env bash
#
# regen_cereal_gen.sh — Mac 侧重生成 openpilot/cereal/gen/cpp 并盖 schema 指纹。
#
# 使用场景：任何 .capnp schema 改动之后（设备无 capnpc 工具链，发布构建用的是
# checked-in 生成物 + SKIP_CAPNP_REGEN=1）。跑完把 gen/cpp 一起提交，否则发布
# 门禁 check-schema-stamp 会拒发。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
cd "$ROOT"

CAPNP_BIN="$ROOT/.venv/lib/python3.12/site-packages/capnproto/install/bin"
if [ ! -x "$CAPNP_BIN/capnpc" ]; then
  echo "找不到 capnpc（$CAPNP_BIN）——需要 comma-deps-capnproto（项目 venv）" >&2
  exit 1
fi
export PATH="$CAPNP_BIN:$PATH"

# schema 清单不在这里手写：唯一登记表在 openpilot/cereal/schemas.py，
# 经 release_lib.py schema-paths 取（fix/schema-registry ①）。
SCHEMAS=($(python3 tools/release/release_lib.py schema-paths))

capnpc --src-prefix=openpilot/cereal --src-prefix=opendbc_repo/opendbc/car \
  --import-path=opendbc_repo/opendbc/car \
  "${SCHEMAS[@]}" -o c++:openpilot/cereal/gen/cpp/

python3 tools/release/release_lib.py stamp-schemas openpilot/cereal/gen/cpp "${SCHEMAS[@]}"
echo "[ok] gen/cpp 重生成 + schema 指纹已盖（记得连同 gen/cpp 一起提交）"
