"""Whether the tree is fit to skip build.py on the next boot.

launch_chffrplus.sh runs build.py only when `prebuilt` is absent, so that
marker is an instruction ("do not rebuild"), not a record ("build finished").
Writing it while the tree holds missing or cross-built native artifacts pins
the device to exactly that state: nothing rebuilds them, and
harvest_device_prebuilt.sh would copy them back into the next release.

A container-built pandad reached a car this way. common/hardware/hw.h selects
paths at compile time and SConstruct defines __COMMA_HARDWARE__ only when the
BUILD machine has /AGNOS, so the container build baked in
Path::comma_home() -- it read $HOME/.comma/params while card wrote
/data/params. The OBD multiplexing handshake never completed, CarParams was
never published, and the screen sat at "sunnypilot unavailable, waiting to
start".

Kept free of openpilot.common.params so it imports without the native params
library (and is therefore testable off-device).
"""
from __future__ import annotations

import os

# Native daemons that must exist and be device-built before skipping build.py.
QUICK_BOOT_NATIVE_ARTIFACTS = (
  "openpilot/selfdrive/pandad/pandad",
  "openpilot/system/loggerd/loggerd",
  "openpilot/system/camerad/camerad",
  "openpilot/sunnypilot/selfdrive/locationd/locationd",
  "openpilot/common/libparams_c.so",
)

# Cross-built artifacts contain Path::comma_home() ("$HOME/.comma/...").
# Device-built ones never do. Mirrors PC_PATH_MARKER in tools/release/release_lib.py.
PC_PATH_MARKER = b"/.comma"


def native_artifacts_unfit_for_quick_boot(basedir: str) -> str | None:
  """Return why the marker must not be written, or None when it is safe."""
  for rel in QUICK_BOOT_NATIVE_ARTIFACTS:
    path = os.path.join(basedir, rel)
    if not os.path.isfile(path):
      return f"missing native artifact: {rel}"
    try:
      with open(path, "rb") as f:
        if PC_PATH_MARKER in f.read():
          return f"cross-built native artifact: {rel}"
    except OSError as e:
      return f"unreadable native artifact: {rel} ({e})"
  return None
