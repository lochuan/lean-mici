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
import re
import subprocess
import sys
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


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  stamp = sub.add_parser("stamp-tinygrad-pin", help="write the tinygrad pin from the source gitlink into the stage")
  stamp.add_argument("stage")
  stamp.add_argument("source_repo")
  check = sub.add_parser("check-stage", help="publish gate: tinygrad pin resolvable, no oversized files")
  check.add_argument("stage")
  args = parser.parse_args()

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
