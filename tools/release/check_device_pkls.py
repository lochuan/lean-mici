#!/usr/bin/env python3
"""Verify every pkl shipped with this release still loads on this device.

Runs ON the comma device. A pkl embeds tinygrad's pickled object graph, so it
is only loadable by the tinygrad revision that wrote it -- and nothing else in
the release pipeline checks that:

  * ``release_lib.py`` covers ``release/prebuilt`` through native_hash, which
    includes the ``tinygrad_repo`` gitlink, so a bumped submodule at least
    degrades the release to source-only. But that only guards the *driving*
    pkl, and only by refusing to ship prebuilts -- it never checks that the
    pkl can actually be read.
  * ``eagled``'s YOLO pkl is committed as plain source. It is in no
    manifest, in no NATIVE_INPUT_PATHS entry, and no guard looks at it at all.

That gap shipped twice. Bumping tinygrad_repo e837e367a -> 0811d78ea in
eab754fc7 left the prebuilt driving pkl unreadable
("AttributeError: 'int' object has no attribute '_base'"), and the YOLO pkl
committed in 76dcfaaed was built against the *new* tinygrad while lean-release
still pinned the old one, so it raised
"TypeError: CallInfo.__init__() takes from 1 to 6 positional arguments but 7
were given" on every eagled start. The feature had never run end to end on
the release branch and no gate noticed.

This has to run on the device: unpickling a QCOM pkl touches the QCOM device
(it opens /dev/kgsl-3d0), so a container or Mac cannot do it.

Usage (on the device, or via smoke_gate.sh):
    PYTHONPATH=/data/openpilot python3 check_device_pkls.py [--root /data/openpilot]

Exit code 0 when every discovered pkl loads, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

# Shipped pkls, relative to the openpilot checkout root. Chunked artifacts are
# named by their logical path; open_file_chunked resolves the chunk set.
PKL_PATHS: tuple[str, ...] = (
  "openpilot/selfdrive/modeld/models/driving_tinygrad.pkl",
  "openpilot/selfdrive/eagled/models/yolo_tinygrad.pkl",
)


def _exists(root: Path, rel: str) -> bool:
  """True if the pkl is present, either whole or as a chunk set."""
  path = root / rel
  return path.is_file() or Path(f"{path}.chunkmanifest").is_file()


def check(root: Path) -> int:
  # Imported lazily so --help works off-device.
  from openpilot.common.file_chunker import open_file_chunked
  from openpilot.selfdrive.modeld.helpers import load_oob
  import tinygrad

  print(f"tinygrad: {tinygrad.__file__}")
  print(f"root    : {root}")

  checked = failed = 0
  for rel in PKL_PATHS:
    if not _exists(root, rel):
      print(f"  SKIP {rel} (not present)")
      continue
    checked += 1
    try:
      jits = load_oob(open_file_chunked(str(root / rel)))
    except Exception:  # noqa: BLE001 - any failure means the artifact is unusable
      failed += 1
      print(f"  FAIL {rel}")
      traceback.print_exc()
      continue
    detail = f"keys={sorted(jits)}" if isinstance(jits, dict) else type(jits).__name__
    print(f"  OK   {rel}  {detail}")

  if checked == 0:
    print("PKL CHECK: FAIL (no pkl found -- wrong --root?)", file=sys.stderr)
    return 1
  if failed:
    print(f"PKL CHECK: FAIL ({failed}/{checked} unreadable)", file=sys.stderr)
    print("  The tinygrad revision this checkout pins cannot read these artifacts.", file=sys.stderr)
    print("  Rebuild them against it: scons for the driving pkl, and", file=sys.stderr)
    print("  selfdrive/eagled/models/compile_yolo_onnx.py for the YOLO pkl.", file=sys.stderr)
    return 1
  print(f"PKL CHECK: PASS ({checked} artifact(s))")
  return 0


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--root", default=os.environ.get("OPENPILOT_ROOT", "/data/openpilot"),
                  type=Path, help="openpilot checkout root (default /data/openpilot)")
  args = ap.parse_args()
  if not args.root.is_dir():
    print(f"PKL CHECK: FAIL (no such root: {args.root})", file=sys.stderr)
    return 1
  return check(args.root)


if __name__ == "__main__":
  raise SystemExit(main())
