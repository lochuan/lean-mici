#!/usr/bin/env python3
"""LANLink 前端产物的陈旧检查。

设备上没有 Node，所以 `openpilot/system/lanlinkd/static/` 里的构建产物是
**随 git 提交**的（见 web/README.md）。这带来一个安静的失败模式：改了
`web/src/` 却忘记 `npm run build`，release 照常打包，设备上跑的还是旧界面——
没有任何报错，只是行为对不上代码。

做法与 tools/release/release_lib.py 的 native_hash 一致：把所有前端输入的
git tree 条目哈希成一个指纹，构建时写进 static/.build-hash。检查时重算并
比对，不一致就报错。

用 `git ls-tree` 而不是读文件内容，是为了跟 release 打包的口径一致：
release 只发 tracked 文件，所以**只有已提交的源码**才算数。改了但没 add
的文件不会改变指纹——这正是我们要的，否则本地脏工作区会一直告警。

用法：
  tools/build_lanlink_web.py --check    # 校验（CI / 提交前）
  tools/build_lanlink_web.py --write    # 构建后写入指纹
  tools/build_lanlink_web.py --build    # npm ci（按需）+ npm run build + 写指纹
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "openpilot/system/lanlinkd/web"
STATIC_DIR = REPO_ROOT / "openpilot/system/lanlinkd/static"
HASH_FILE = STATIC_DIR / ".build-hash"

# 影响构建产物的一切输入。锁文件也算：依赖版本变了产物就可能变。
SOURCE_PATHS: tuple[str, ...] = (
  "openpilot/system/lanlinkd/web/src",
  "openpilot/system/lanlinkd/web/index.html",
  "openpilot/system/lanlinkd/web/package.json",
  "openpilot/system/lanlinkd/web/package-lock.json",
  "openpilot/system/lanlinkd/web/vite.config.ts",
  "openpilot/system/lanlinkd/web/tsconfig.json",
)


def prune_stale_assets() -> list[str]:
  """删掉 static/assets/ 里已经没人引用的旧 hash 文件。

  Vite 的文件名带内容 hash，每次构建换名字；而 outDir 用的是
  emptyOutDir: false（static/ 下还有 .build-hash 等非构建文件不能删），
  所以旧产物会一直堆积——实测堆了 3 份，还都被 git add 进去了。
  这些文件带 immutable 长缓存、随 release 发到设备，纯属白占空间。
  """
  assets = STATIC_DIR / "assets"
  index = STATIC_DIR / "index.html"
  if not assets.is_dir() or not index.exists():
    return []

  referenced = set(re.findall(r"/assets/([A-Za-z0-9._\-]+)", index.read_text()))
  if not referenced:
    raise RuntimeError("index.html 没有引用任何 /assets/ 文件，产物可能损坏，拒绝清理")

  removed = []
  for f in sorted(assets.iterdir()):
    if f.is_file() and f.name not in referenced:
      f.unlink()
      removed.append(f.name)
  return removed


def compute_source_hash(repo_root: Path = REPO_ROOT) -> str:
  """把前端输入的 git 索引条目哈希成一个指纹。

  读的是**索引**（git ls-files -s）而不是 HEAD 的 tree：源码改动和 static/
  产物通常在同一个提交里一起 add，若按 HEAD 计算，暂存区里的新源码根本看不见，
  指纹不会变，陈旧检查就永远通过——这正是我们要防的情况。

  仍然以 git 为准（而非直接读工作区文件），因为 release 只发 tracked 文件：
  没 add 的改动不会进设备，也就不该让检查失败。
  """
  entries = subprocess.run(
    ["git", "ls-files", "-s", "--", *SOURCE_PATHS],
    cwd=repo_root, capture_output=True, text=True, check=True,
  ).stdout
  if not entries.strip():
    raise RuntimeError("没有找到任何已跟踪的前端源码，SOURCE_PATHS 是否写错？")
  return subprocess.run(
    ["git", "hash-object", "--stdin"],
    cwd=repo_root, capture_output=True, text=True, input=entries, check=True,
  ).stdout.strip()


def read_recorded_hash() -> str | None:
  if not HASH_FILE.exists():
    return None
  return HASH_FILE.read_text().strip() or None


def write_hash(value: str) -> None:
  STATIC_DIR.mkdir(parents=True, exist_ok=True)
  HASH_FILE.write_text(value + "\n")


def check() -> int:
  if not (STATIC_DIR / "index.html").exists():
    print("错误：static/index.html 不存在，前端还没构建过。", file=sys.stderr)
    print("  运行：tools/build_lanlink_web.py --build", file=sys.stderr)
    return 1

  recorded = read_recorded_hash()
  expected = compute_source_hash()

  if recorded is None:
    print("错误：static/.build-hash 缺失，无法判断产物是否为最新。", file=sys.stderr)
    print("  运行：tools/build_lanlink_web.py --build", file=sys.stderr)
    return 1

  if recorded != expected:
    print("错误：前端产物已过期——web/ 下的源码比 static/ 里的构建更新。", file=sys.stderr)
    print(f"  产物记录: {recorded}", file=sys.stderr)
    print(f"  源码实际: {expected}", file=sys.stderr)
    print("  设备上没有 Node，发版只会带上 static/，所以旧产物会被静默发出去。", file=sys.stderr)
    print("  运行：tools/build_lanlink_web.py --build && git add openpilot/system/lanlinkd/static", file=sys.stderr)
    return 1

  print(f"LANLink 前端产物是最新的 ({recorded[:12]})")
  return 0


def build() -> int:
  npm = shutil.which("npm")
  if npm is None:
    print("错误：找不到 npm。前端构建只能在开发机上做（设备上没有 Node）。", file=sys.stderr)
    return 1

  if not (WEB_DIR / "node_modules").exists():
    print("node_modules 不存在，先按 lockfile 还原依赖…")
    subprocess.run([npm, "ci"], cwd=WEB_DIR, check=True)

  subprocess.run([npm, "run", "build"], cwd=WEB_DIR, check=True)

  stale = prune_stale_assets()
  for name in stale:
    print(f"已删除失效产物 assets/{name}")

  # 指纹按 git 索引算，所以源码必须先 add。未 add 的改动不会进 release，
  # 也就不该计入指纹——但要提示用户，否则容易漏提交源码只提交了产物。
  unstaged = subprocess.run(
    ["git", "diff", "--name-only", "--", *SOURCE_PATHS],
    cwd=REPO_ROOT, capture_output=True, text=True, check=True,
  ).stdout.strip()

  value = compute_source_hash()
  write_hash(value)
  print(f"已写入 static/.build-hash = {value}")

  if unstaged:
    print("\n注意：以下前端源码有未暂存（未 git add）的改动：", file=sys.stderr)
    for line in unstaged.splitlines():
      print(f"  {line}", file=sys.stderr)
    print("指纹是按 git 索引算的，这些改动没有计入。", file=sys.stderr)
    print("请 git add 源码后重新运行本脚本，再把 static/ 一起提交。", file=sys.stderr)
    return 1

  return 0


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  g = ap.add_mutually_exclusive_group(required=True)
  g.add_argument("--check", action="store_true", help="校验产物是否与源码一致")
  g.add_argument("--write", action="store_true", help="只写指纹（假定刚构建过）")
  g.add_argument("--build", action="store_true", help="构建并写指纹")
  args = ap.parse_args()

  if args.check:
    return check()
  if args.write:
    value = compute_source_hash()
    write_hash(value)
    print(f"已写入 static/.build-hash = {value}")
    return 0
  return build()


if __name__ == "__main__":
  sys.exit(main())
