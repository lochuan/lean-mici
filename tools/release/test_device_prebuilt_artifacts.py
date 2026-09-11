#!/usr/bin/env python3
"""Static checks for the lean release scripts."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "tools/release/build_lean_release_on_orb.sh"
HARVEST_SCRIPT = ROOT / "tools/release/harvest_device_prebuilt.sh"
HASH_SCRIPT = ROOT / "tools/release/prebuilt_native_hash.sh"
RELEASE_LIB = ROOT / "tools/release/release_lib.py"


def _load_release_lib():
  spec = importlib.util.spec_from_file_location("release_lib", RELEASE_LIB)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class TestBuildScript(unittest.TestCase):
  def setUp(self):
    self.script = BUILD_SCRIPT.read_text()

  def test_does_not_overlay_container_elf(self):
    release_section = self.script.split("--- 6. push lean-release", 1)[1]
    self.assertNotIn("elf_find.py", release_section)

  def test_only_panda_firmware_is_overlaid_from_container(self):
    self.assertIn("*.bin", self.script)
    self.assertIn("*.bin.signed", self.script)

  def test_calls_release_lib_overlay(self):
    self.assertIn("release_lib.py overlay /tmp/opilot-release", self.script)

  def test_submodule_guard_is_fatal(self):
    self.assertIn('if [ "$status" -ne 0 ]; then', self.script)
    self.assertIn('exit "$status"', self.script)

  def test_push_failure_is_fatal(self):
    self.assertIn("pushed=0", self.script)
    self.assertIn("-ne 1", self.script)

  def test_commit_noop_is_distinguished_from_failure(self):
    self.assertIn("git diff --cached --quiet", self.script)


class TestHarvestScript(unittest.TestCase):
  def setUp(self):
    self.script = HARVEST_SCRIPT.read_text()

  def test_uses_release_lib_artifact_paths(self):
    self.assertIn("release_lib.py", self.script)
    self.assertIn("artifact-paths", self.script)

  def test_validates_artifacts(self):
    self.assertIn("release_lib.py", self.script)
    self.assertIn("validate-artifacts", self.script)

  def test_writes_manifest(self):
    self.assertIn("release_lib.py", self.script)
    self.assertIn("write-manifest", self.script)

  def test_prints_git_add_for_ignored_so(self):
    self.assertIn("git add -f release/prebuilt/arm64/openpilot/common/libparams_c.so", self.script)


class TestHashScript(unittest.TestCase):
  def test_delegates_to_release_lib(self):
    text = HASH_SCRIPT.read_text()
    self.assertIn("release_lib.py", text)
    self.assertIn(" hash ", text)


class TestReleaseLibArtifactSet(unittest.TestCase):
  def test_artifact_set_matches_expected_runtime_files(self):
    release_lib = _load_release_lib()
    expected = {
      "openpilot/common/libparams_c.so",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/acados_ocp_solver_pyx.so",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados.so",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libacados_ocp_solver_long.so",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libblasfeo.so",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libhpipm.so",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib/c_generated_code/libqpOASES_e.so.3.1",
      "openpilot/selfdrive/locationd/models/generated/libcar.so",
      "openpilot/selfdrive/locationd/models/generated/libpose.so",
      "openpilot/selfdrive/pandad/pandad",
      "openpilot/sunnypilot/selfdrive/locationd/locationd",
      "openpilot/sunnypilot/selfdrive/locationd/models/generated/liblive.so",
      "openpilot/system/camerad/camerad",
      "openpilot/system/loggerd/loggerd",
    }
    self.assertEqual(set(release_lib.ARTIFACT_PATHS), expected)


if __name__ == "__main__":
  unittest.main()
