#!/usr/bin/env bash
# prebuilt_native_hash.sh <commit> — camerad/loggerd 原生依赖源码的树哈希。
# harvest（mac 端回收）与 release 构建（容器）共用同一算法，保证守卫判定一致。
# 覆盖：两个 daemon 源码 + 链接的 in-tree 库（common/cereal/msgq）+ 构建开关（SConstruct）。
# submodule（msgq_repo）以 gitlink 指针入哈希，指针变即失效。
set -e
commit="${1:-HEAD}"
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" ls-tree -r "$commit" -- \
  openpilot/system/camerad \
  openpilot/system/loggerd \
  openpilot/common \
  openpilot/cereal \
  msgq_repo \
  SConstruct \
  | git -C "$ROOT" hash-object --stdin
