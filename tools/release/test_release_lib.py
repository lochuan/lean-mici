#!/usr/bin/env python3
"""Unit tests for tools/release/release_lib.py."""
from __future__ import annotations

import importlib.util
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "release_lib.py"
spec = importlib.util.spec_from_file_location("release_lib", MODULE_PATH)
release_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_lib)

ARTIFACT_PATHS = release_lib.ARTIFACT_PATHS
NATIVE_INPUT_PATHS = release_lib.NATIVE_INPUT_PATHS
compute_native_hash = release_lib.compute_native_hash
is_arm64_elf = release_lib.is_arm64_elf
overlay_prebuilt = release_lib.overlay_prebuilt
read_manifest = release_lib.read_manifest
validate_artifact = release_lib.validate_artifact
write_manifest = release_lib.write_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]


def elf_bytes(machine: int, bits: int = 64) -> bytes:
  if bits == 64:
    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    return struct.pack(
      "<16sHHIQQQIHHHHHH",
      ident,
      2,
      machine,
      1,
      0,
      0,
      0,
      0,
      64,
      0,
      0,
      0,
      0,
      0,
    )
  ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
  return struct.pack(
    "<16sHHIIIIIHHHHHH",
    ident,
    2,
    machine,
    1,
    0,
    0,
    0,
    0,
    52,
    0,
    0,
    0,
    0,
    0,
  )


def git(repo: Path, *args: str) -> str:
  return subprocess.check_output(
    ["git", "-C", str(repo), *args],
    text=True,
  ).strip()


class TestConstants(unittest.TestCase):
  def test_artifact_paths_are_complete(self):
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
    self.assertEqual(set(ARTIFACT_PATHS), expected)
    self.assertEqual(len(ARTIFACT_PATHS), len(expected))

  def test_native_input_paths_are_complete(self):
    expected = {
      "SConstruct",
      "site_scons",
      "openpilot/common",
      "openpilot/cereal",
      "openpilot/selfdrive/controls/lib/longitudinal_mpc_lib",
      "openpilot/selfdrive/locationd/models/generated",
      "openpilot/selfdrive/pandad",
      "openpilot/sunnypilot/selfdrive/locationd",
      "openpilot/system/camerad",
      "openpilot/system/loggerd",
      "msgq_repo",
      "opendbc_repo",
      "rednose_repo",
      "panda",
    }
    self.assertEqual(set(NATIVE_INPUT_PATHS), expected)
    self.assertEqual(len(NATIVE_INPUT_PATHS), len(expected))


class TestElfValidation(unittest.TestCase):
  def test_accepts_arm64_elf(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(elf_bytes(183))
      self.assertTrue(is_arm64_elf(p))

  def test_rejects_non_elf(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(b"not an elf")
      self.assertFalse(is_arm64_elf(p))

  def test_rejects_32bit_elf(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(elf_bytes(183, bits=32))
      self.assertFalse(is_arm64_elf(p))

  def test_rejects_x86_64_elf(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(elf_bytes(62))
      self.assertFalse(is_arm64_elf(p))


class TestNativeHash(unittest.TestCase):
  def test_hash_changes_when_native_input_changes(self):
    with tempfile.TemporaryDirectory() as td:
      repo = Path(td)
      git(repo, "init")
      git(repo, "config", "user.email", "test@example.com")
      git(repo, "config", "user.name", "test")
      common = repo / "openpilot/common"
      common.mkdir(parents=True)
      (common / "params.cc").write_text("int one() { return 1; }\n")
      git(repo, "add", ".")
      git(repo, "commit", "-m", "initial")
      first = compute_native_hash(repo, "HEAD")
      (common / "params.cc").write_text("int one() { return 2; }\n")
      git(repo, "add", ".")
      git(repo, "commit", "-m", "change")
      second = compute_native_hash(repo, "HEAD")
      self.assertNotEqual(first, second)


class TestManifestAndArtifactValidation(unittest.TestCase):
  def test_manifest_round_trip(self):
    with tempfile.TemporaryDirectory() as td:
      dest = Path(td)
      artifact = dest / "openpilot/common/libparams_c.so"
      artifact.parent.mkdir(parents=True)
      artifact.write_bytes(elf_bytes(183))
      write_manifest(dest, "abc123", "def456", ["openpilot/common/libparams_c.so"])
      manifest = read_manifest(dest / "MANIFEST")
      self.assertEqual(manifest["source_commit"], "abc123")
      self.assertEqual(manifest["native_hash"], "def456")
      self.assertEqual(
        manifest["files"],
        "openpilot/common/libparams_c.so",
      )
      self.assertIn("sha256.openpilot/common/libparams_c.so", manifest)

  def test_validate_artifact_accepts_matching_arm64_file(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(elf_bytes(183))
      ok, reason = validate_artifact(p)
      self.assertTrue(ok, reason)

  def test_validate_artifact_rejects_missing_file(self):
    with tempfile.TemporaryDirectory() as td:
      ok, reason = validate_artifact(Path(td) / "missing.so")
      self.assertFalse(ok)
      self.assertIn("missing", reason)

  def test_validate_artifact_rejects_checksum_mismatch(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(elf_bytes(183))
      ok, reason = validate_artifact(p, expected_sha256="0" * 64)
      self.assertFalse(ok)
      self.assertIn("checksum", reason)

  def test_validate_artifact_rejects_non_arm64_elf(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(elf_bytes(62))
      ok, reason = validate_artifact(p)
      self.assertFalse(ok)
      self.assertIn("ARM64", reason)


class TestOverlayPrebuilt(unittest.TestCase):
  def _make_repo(self, td: Path) -> Path:
    repo = td / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    for rel in NATIVE_INPUT_PATHS:
      path = repo / rel
      if rel in {"msgq_repo", "opendbc_repo", "rednose_repo", "panda"}:
        path.mkdir()
        (path / "placeholder").write_text(rel + "\n")
      elif rel == "SConstruct":
        path.write_text("# fake SConstruct\n")
      else:
        path.mkdir(parents=True, exist_ok=True)
        (path / "file.txt").write_text(rel + "\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    return repo

  def _make_worktree(self, td: Path, repo: Path) -> Path:
    worktree = td / "worktree"
    subprocess.check_call(
      ["git", "-C", str(repo), "worktree", "add", str(worktree), "HEAD"],
      stdout=subprocess.DEVNULL,
    )
    return worktree

  def _populate_prebuilt(self, worktree: Path, machine: int = 183) -> None:
    prebuilt_root = worktree / "release/prebuilt/arm64"
    for rel in ARTIFACT_PATHS:
      src = prebuilt_root / rel
      src.parent.mkdir(parents=True, exist_ok=True)
      src.write_bytes(elf_bytes(machine))
    native_hash = compute_native_hash(worktree, "HEAD")
    write_manifest(prebuilt_root, "abc123", native_hash, list(ARTIFACT_PATHS))

  def test_overlay_succeeds_with_valid_artifacts(self):
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree)
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertTrue(ok, reason)
      self.assertTrue((worktree / "prebuilt").exists())
      self.assertFalse((worktree / "release/prebuilt").exists())
      for rel in ARTIFACT_PATHS:
        self.assertTrue((worktree / rel).exists(), rel)

  def test_overlay_fails_on_native_hash_mismatch(self):
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree)
      manifest_path = worktree / "release/prebuilt/arm64/MANIFEST"
      manifest = read_manifest(manifest_path)
      manifest["native_hash"] = "0" * 40
      lines = [
        f"source_commit={manifest['source_commit']}",
        f"native_hash={manifest['native_hash']}",
        f"files={manifest['files']}",
      ]
      lines += [
        f"{key}={value}"
        for key, value in manifest.items()
        if key.startswith("sha256.")
      ]
      manifest_path.write_text("\n".join(lines) + "\n")
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertFalse(ok)
      self.assertIn("native hash", reason)
      self.assertFalse((worktree / "prebuilt").exists())

  def test_overlay_fails_on_missing_artifact(self):
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree)
      missing = worktree / "release/prebuilt/arm64" / ARTIFACT_PATHS[0]
      missing.unlink()
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertFalse(ok)
      self.assertIn("missing", reason)
      self.assertFalse((worktree / "prebuilt").exists())

  def test_overlay_fails_on_checksum_mismatch(self):
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree)
      artifact = worktree / "release/prebuilt/arm64" / ARTIFACT_PATHS[0]
      artifact.write_bytes(elf_bytes(183) + b"\x00")
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertFalse(ok)
      self.assertIn("checksum", reason)
      self.assertFalse((worktree / "prebuilt").exists())

  def test_overlay_fails_on_non_arm64_elf(self):
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree, machine=62)
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertFalse(ok)
      self.assertIn("ARM64", reason)
      self.assertFalse((worktree / "prebuilt").exists())


if __name__ == "__main__":
  unittest.main()
