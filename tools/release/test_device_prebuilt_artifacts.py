#!/usr/bin/env python3
"""Regression checks for artifacts that must be built on comma hardware."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
HARVEST_SCRIPT = ROOT / "tools/release/harvest_device_prebuilt.sh"
BUILD_SCRIPT = ROOT / "tools/release/build_lean_release_on_orb.sh"


class TestDevicePrebuiltArtifacts(unittest.TestCase):
  def test_libparams_is_harvested_and_excluded_from_container_overlay(self):
    """PC-built libparams uses the wrong default Params root on comma hardware."""
    harvest = HARVEST_SCRIPT.read_text()
    build = BUILD_SCRIPT.read_text()

    self.assertIn("openpilot/common/libparams_c.so", harvest)
    self.assertIn("openpilot/common/libparams_c.so", build)


if __name__ == "__main__":
  unittest.main()
