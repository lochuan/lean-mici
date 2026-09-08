#!/usr/bin/env python3
"""NUL-separated list of all ELF files under cwd (release artifact overlay).

Covers extensionless daemons (pandad, loggerd, camerad, locationd) and
versioned shared libs (*.so.3.1) that name-glob patterns miss.
"""
import os
import sys

for root, dirs, files in os.walk("."):
  if ".git" in root.split(os.sep):
    dirs[:] = []
    continue
  for f in files:
    # skip intermediate object files (.o/.os); runtime needs .so, .so.X and executables
    if f.endswith(".o") or f.endswith(".os"):
      continue
    p = os.path.join(root, f)
    try:
      with open(p, "rb") as fh:
        if fh.read(4) == b"\x7fELF":
          sys.stdout.write(p + "\0")
    except OSError:
      pass
sys.stdout.flush()
