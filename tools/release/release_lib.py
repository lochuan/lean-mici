#!/usr/bin/env python3
"""Helpers for the device-built lean release (called by device_release.sh).

The release tree is the clean build tree (/data/build) stripped to runtime
files. Its completeness comes from the build itself (a full ``scons`` in a
tree that ``git clean`` resets to the source commit), so this module only
owns what the stripped tree cannot derive on its own: the tinygrad pin, and
the publish gate on the stage tree.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

TINYGRAD_PIN_FILE = "TINYGRAD_PIN"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# GitHub rejects files over 100 MB; stay under it with the same margin as upstream's build_stripped.sh.
MAX_FILE_BYTES = 95 * 1024 * 1024


def stamp_tinygrad_pin(stage: Path, source_repo: Path) -> str:
  """Record the tinygrad_repo revision pinned by ``source_repo``'s HEAD into the staged tree.

  The published tree strips tinygrad_repo/.git, and the model selector's runtime
  gating (openpilot/sunnypilot/models/tinygrad_ref.py) falls back to this file.
  The pin is read from the gitlink (``git ls-tree``), the source of truth.
  """
  result = subprocess.run(["git", "-C", str(source_repo), "ls-tree", "HEAD", "tinygrad_repo"],
                          check=True, capture_output=True, text=True)
  fields = result.stdout.split()
  if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit":
    raise ValueError(
      f"tinygrad_repo is not a gitlink in {source_repo}: {result.stdout.strip()!r}")
  sha = fields[2]
  dest = stage / "tinygrad_repo" / TINYGRAD_PIN_FILE
  dest.parent.mkdir(parents=True, exist_ok=True)
  dest.write_text(sha + "\n")
  return sha


def read_stage_tinygrad_pin(stage: Path) -> str | None:
  """Resolve the staged tree's tinygrad pin; None when absent or malformed."""
  try:
    sha = (stage / "tinygrad_repo" / TINYGRAD_PIN_FILE).read_text().strip()
  except OSError:
    return None
  return sha if _SHA_RE.match(sha) else None


def check_stage(stage: Path) -> list[str]:
  """Publish gate on the stage tree; returns problems, empty = pass."""
  problems = []
  if read_stage_tinygrad_pin(stage) is None:
    problems.append("stage tree cannot resolve its tinygrad pin")
  for p in sorted(stage.rglob("*")):
    if p.is_file() and not p.is_symlink() and p.stat().st_size > MAX_FILE_BYTES:
      problems.append(f"file exceeds GitHub's size limit: {p.relative_to(stage)}")
  return problems


def send_local_source(source_repo: Path, device: str, expected_source_sha: str) -> str:
  """Stage exact local nav-test/gitlink commits under the device build repo, without public remotes."""
  def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source_repo), *args], text=True).strip()

  if not _SHA_RE.fullmatch(expected_source_sha):
    raise ValueError("EXPECTED_SOURCE_SHA must contain exactly 40 lowercase hex characters")
  revision = git("rev-parse", "refs/heads/nav-test^{commit}")
  if revision != expected_source_sha:
    raise ValueError(f"EXPECTED_SOURCE_SHA mismatch: expected {expected_source_sha}, refs/heads/nav-test is {revision}")
  common = Path(git("rev-parse", "--git-common-dir"))
  if not common.is_absolute():
    common = source_repo / common
  common = common.resolve()
  sources = [(common, revision)]
  paths = git("config", "--blob", f"{revision}:.gitmodules", "--get-regexp", r"\.path$")
  for line in paths.splitlines():
    key, path = line.split(None, 1)
    name = key[len("submodule."):-len(".path")]
    sources.append((common / "modules" / name, git("rev-parse", f"{revision}:{path}")))

  # Prepare every dependency before writing the device; missing local objects fail here.
  with tempfile.TemporaryDirectory(prefix="local-release-", dir="/tmp") as temporary:
    payloads = []
    for source_git, sha in sources:
      bare = Path(temporary) / sha
      subprocess.run(["git", "init", "-q", "--bare", "--template=", str(bare)], check=True)
      subprocess.run(["git", "-C", str(bare), "fetch", "-q", "--depth=1", source_git.as_uri(),
                      f"{sha}:refs/heads/source"], check=True)
      archive = Path(temporary) / f"{sha}.tar"
      with tarfile.open(archive, "w") as stream:
        stream.add(bare, arcname=".")
      payloads.append((sha, archive))
    cache_script = """
import json, pathlib, re, shutil, sys
root = pathlib.Path('/data/build/.git/local-source')
root.mkdir(parents=True, exist_ok=True)
index = root / 'generations.json'
old = json.loads(index.read_text()) if index.exists() else {}
current, commits = sys.argv[1], json.loads(sys.argv[2])
previous = old.get('previous', []) if old.get('current') == current else old.get('commits', [])
keep = set(commits + previous)
for path in root.iterdir():
  if path.is_dir() and re.fullmatch('[0-9a-f]{40}', path.name) and path.name not in keep:
    shutil.rmtree(path)
index.write_text(json.dumps({'current': current, 'commits': commits, 'previous': previous}))
"""
    subprocess.run(["ssh", device, "git init -q /data/build && " + shlex.join(
      ["/usr/local/venv/bin/python", "-c", cache_script, revision, json.dumps([sha for sha, _ in payloads])])],
      stdout=subprocess.DEVNULL, check=True)
    for sha, archive in payloads:
      destination = shlex.quote(f"/data/build/.git/local-source/{sha}")
      command = " && ".join([
        "git init -q /data/build",
        "git -C /data/build config remote.origin.url https://github.com/lochuan/lean-mici.git",
        f"mkdir -p {destination}",
        f"tar -xf - -C {destination}",
        f"git --git-dir={destination} cat-file -e {sha}^{{commit}}",
      ])
      with archive.open("rb") as stream:
        subprocess.run(["ssh", device, command], stdin=stream, stdout=subprocess.DEVNULL, check=True)
  return revision


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  stamp = sub.add_parser("stamp-tinygrad-pin", help="write the tinygrad pin from the source gitlink into the stage")
  stamp.add_argument("stage")
  stamp.add_argument("source_repo")
  check = sub.add_parser("check-stage", help="publish gate: tinygrad pin resolvable, no oversized files")
  check.add_argument("stage")
  local = sub.add_parser("send-local-source", help="stage local nav-test and pinned submodules over SSH")
  local.add_argument("source_repo", type=Path)
  local.add_argument("device")
  local.add_argument("expected_source_sha")
  args = parser.parse_args()

  if args.command == "send-local-source":
    print(send_local_source(args.source_repo, args.device, args.expected_source_sha))
    return 0

  if args.command == "stamp-tinygrad-pin":
    sha = stamp_tinygrad_pin(Path(args.stage), Path(args.source_repo))
    print(f"[ok] tinygrad pin stamped: {sha[:7]}")
    return 0

  problems = check_stage(Path(args.stage))
  for problem in problems:
    print(problem, file=sys.stderr)
  return 1 if problems else 0


if __name__ == "__main__":
  raise SystemExit(main())
