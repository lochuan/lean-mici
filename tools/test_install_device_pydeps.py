#!/usr/bin/env python3
"""Tests for the /data/pydeps provisioning contract.

aiohttp used to be assumed present from the AGNOS venv; 19.6 dropped it and
models_manager died on device while CI stayed green. These pin the parts of the
setup that broke, or that would break quietly.
"""
from __future__ import annotations

import re
import stat
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools/install_device_pydeps.sh"
LAUNCH = REPO / "launch_chffrplus.sh"
PYDEPS = "/data/pydeps"


class TestLaunchScript(unittest.TestCase):
  def test_pydeps_is_on_pythonpath(self):
    """Packages in /data/pydeps are only importable if launch puts it on the path."""
    exports = [
      line for line in LAUNCH.read_text().splitlines()
      if "export PYTHONPATH" in line and not line.lstrip().startswith("#")
    ]
    self.assertTrue(exports, "launch_chffrplus.sh sets no PYTHONPATH")
    for line in exports:
      self.assertIn(PYDEPS, line, f"PYTHONPATH export omits {PYDEPS}: {line.strip()}")


class TestProvisioningScript(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.src = SCRIPT.read_text()

  def test_is_executable(self):
    self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR, f"{SCRIPT.name} is not executable")

  def test_installs_into_pydeps_not_the_venv(self):
    """The AGNOS venv is read-only and is replaced by AGNOS updates."""
    self.assertIn("--target '$TARGET'", self.src)
    self.assertIn(f"TARGET={PYDEPS}", self.src)

  def test_every_package_is_pinned(self):
    """An unpinned reinstall can silently change the tree under the device."""
    pkgs = re.search(r"PKGS=\(\n(.*?)\n\)", self.src, re.S)
    self.assertIsNotNone(pkgs, "no PKGS array found")
    entries = [ln.strip().strip('"') for ln in pkgs.group(1).splitlines() if ln.strip()]
    self.assertTrue(entries, "PKGS is empty")
    for e in entries:
      self.assertIn("==", e, f"unpinned dependency: {e}")

  def test_sanic_is_installed(self):
    self.assertRegex(self.src, r'"sanic==')

  def test_requires_prebuilt_wheels(self):
    """The device has no compiler; an sdist would fail mid-install."""
    self.assertIn("--only-binary=:all:", self.src)

  def test_prunes_setuptools(self):
    """pydeps precedes site-packages, so a setuptools here shadows the venv's."""
    self.assertIn("rm -rf setuptools", self.src)
    self.assertIn("pkg_resources", self.src)

  def test_verifies_no_venv_shadowing(self):
    self.assertIn("SHADOWS VENV", self.src)
    self.assertIn("site-packages' in setuptools.__file__", self.src)

  def test_asserts_pydeps_precedes_site_packages(self):
    self.assertIn("pydeps must precede site-packages", self.src)

  def test_fails_fast(self):
    self.assertIn("set -euo pipefail", self.src)


class TestNoUndeclaredHttpFramework(unittest.TestCase):
  """Whatever lanlinkd imports must be provisioned, or the daemon dies on start."""

  LANLINKD = REPO / "openpilot/system/lanlinkd/lanlinkd.py"

  def test_lanlinkd_framework_is_provisioned(self):
    if not self.LANLINKD.exists():
      self.skipTest("lanlinkd not present")
    src = self.LANLINKD.read_text()
    provisioned = SCRIPT.read_text()
    for mod in ("aiohttp", "sanic"):
      if re.search(rf"^\s*(from|import)\s+{mod}\b", src, re.M):
        self.assertIn(
          f'"{mod}==', provisioned,
          f"lanlinkd imports {mod} but it is not pinned in install_device_pydeps.sh",
        )


if __name__ == "__main__":
  unittest.main()
