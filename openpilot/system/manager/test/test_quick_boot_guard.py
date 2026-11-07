"""The `prebuilt` marker tells launch_chffrplus.sh to skip build.py.

Writing it while the tree holds missing or cross-built natives pins the device
to that state: nothing rebuilds them, and a harvest copies them back into the
next release. That is how a container-built pandad reached a car, read
$HOME/.comma/params while card wrote /data/params, and deadlocked the OBD
multiplexing handshake into "sunnypilot unavailable, waiting to start".
"""
from __future__ import annotations

import os
import tempfile
import unittest

from openpilot.system.manager.quick_boot import (
  QUICK_BOOT_NATIVE_ARTIFACTS,
  native_artifacts_unfit_for_quick_boot,
)

# Minimal ELF64/aarch64 header, enough to stand in for a real native artifact.
ELF_AARCH64 = b"\x7fELF\x02\x01\x01" + bytes(11) + b"\x03\x00\xb7\x00"


def build_tree(root: str, *, skip: str | None = None, tainted: str | None = None) -> None:
  for rel in QUICK_BOOT_NATIVE_ARTIFACTS:
    if rel == skip:
      continue
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = ELF_AARCH64 + b"\x00/data/params\x00"
    if rel == tainted:
      body = ELF_AARCH64 + b"\x00/home/comma/.comma/params\x00"
    with open(path, "wb") as f:
      f.write(body)


class TestQuickBootGuard(unittest.TestCase):
  def test_device_built_tree_is_fit(self):
    with tempfile.TemporaryDirectory() as tmp:
      build_tree(tmp)
      self.assertIsNone(native_artifacts_unfit_for_quick_boot(tmp))

  def test_cross_built_pandad_is_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
      build_tree(tmp, tainted="openpilot/selfdrive/pandad/pandad")
      reason = native_artifacts_unfit_for_quick_boot(tmp)
      self.assertIsNotNone(reason)
      self.assertIn("cross-built", reason)
      self.assertIn("pandad", reason)

  def test_any_cross_built_artifact_is_rejected(self):
    for rel in QUICK_BOOT_NATIVE_ARTIFACTS:
      with self.subTest(artifact=rel), tempfile.TemporaryDirectory() as tmp:
        build_tree(tmp, tainted=rel)
        reason = native_artifacts_unfit_for_quick_boot(tmp)
        self.assertIsNotNone(reason, f"{rel} slipped through")
        self.assertIn("cross-built", reason)

  def test_missing_artifact_is_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
      build_tree(tmp, skip="openpilot/system/loggerd/loggerd")
      reason = native_artifacts_unfit_for_quick_boot(tmp)
      self.assertIsNotNone(reason)
      self.assertIn("missing", reason)
      self.assertIn("loggerd", reason)

  def test_empty_tree_is_rejected(self):
    with tempfile.TemporaryDirectory() as tmp:
      self.assertIsNotNone(native_artifacts_unfit_for_quick_boot(tmp))


if __name__ == "__main__":
  unittest.main()
