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
      # driving model pkl inputs
      "openpilot/selfdrive/modeld/SConscript",
      "openpilot/selfdrive/modeld/compile_modeld.py",
      "openpilot/selfdrive/modeld/get_model_metadata.py",
      "openpilot/selfdrive/modeld/helpers.py",
      "openpilot/selfdrive/modeld/models",
      "tinygrad_repo",
    }
    self.assertEqual(set(NATIVE_INPUT_PATHS), expected)
    self.assertEqual(len(NATIVE_INPUT_PATHS), len(expected))

  def test_model_pkl_inputs_feed_native_hash(self):
    """A stale pkl must never ship: its build inputs must invalidate native_hash.

    The pkl embeds tinygrad kernels compiled from driving_supercombo.onnx, so a
    change to the model, the compiler, or tinygrad has to force a re-harvest.
    """
    for rel in (
      "openpilot/selfdrive/modeld/models/driving_supercombo.onnx",
      "openpilot/selfdrive/modeld/compile_modeld.py",
      "openpilot/selfdrive/modeld/get_model_metadata.py",
      "openpilot/selfdrive/modeld/SConscript",
      "tinygrad_repo",
    ):
      covered = [p for p in NATIVE_INPUT_PATHS if rel == p or rel.startswith(p + "/")]
      self.assertTrue(covered, f"{rel} does not feed native_hash")

  def test_every_artifact_has_a_native_input(self):
    """Each shipped binary must have its source tracked, or it can go stale."""
    for artifact in ARTIFACT_PATHS:
      covered = [p for p in NATIVE_INPUT_PATHS if artifact == p or artifact.startswith(p + "/")]
      self.assertTrue(covered, f"{artifact} has no corresponding native input path")


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

  def test_validate_artifact_rejects_cross_built_arm64_elf(self):
    """A container-built ARM64 ELF must be rejected.

    SConstruct defines __COMMA_HARDWARE__ only when the BUILD MACHINE has
    /AGNOS. Cross-building without it makes common/hardware/hw.h bake in
    Path::comma_home() ("$HOME/.comma/params") instead of "/data/params".
    Such a binary is a perfectly valid ARM64 ELF, so is_arm64_elf() passes it --
    that is exactly how a PC-built pandad shipped and deadlocked the OBD
    multiplexing handshake on a real car.
    """
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "pandad"
      p.write_bytes(elf_bytes(183) + b"\x00/home/comma/.comma/params\x00")
      self.assertTrue(is_arm64_elf(p), "precondition: arch check alone passes")
      ok, reason = validate_artifact(p)
      self.assertFalse(ok)
      self.assertIn("PC-built", reason)

  def test_validate_artifact_accepts_device_built_arm64_elf(self):
    """A device-built ELF has no $HOME/.comma paths and must pass."""
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "pandad"
      p.write_bytes(elf_bytes(183) + b"\x00/data/params\x00")
      ok, reason = validate_artifact(p)
      self.assertTrue(ok, reason)

  def test_pc_path_marker_not_applied_to_data_artifacts(self):
    """The PC-path screen is ELF-only: the pkl may legitimately contain paths."""
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "driving_tinygrad.pkl.chunk01of02"
      p.write_bytes(b"pickled kernels /.comma whatever")
      ok, reason = validate_artifact(p, require_elf=False)
      self.assertTrue(ok, reason)


class TestDataArtifacts(unittest.TestCase):
  """The driving model pkl ships as a non-ELF prebuilt: checksum-verified only.

  It embeds tinygrad kernels compiled for the device's QCOM backend, so it
  cannot be cross-built in the release container.
  """

  PKL = "openpilot/selfdrive/modeld/models/driving_tinygrad.pkl.chunk01of02"

  def test_validate_artifact_accepts_non_elf_when_elf_not_required(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "driving_tinygrad.pkl.chunk01of02"
      p.write_bytes(b"not an elf, just pickled kernels")
      ok, reason = validate_artifact(p, require_elf=False)
      self.assertTrue(ok, reason)

  def test_validate_artifact_still_rejects_non_elf_by_default(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "lib.so"
      p.write_bytes(b"not an elf")
      ok, reason = validate_artifact(p)
      self.assertFalse(ok)
      self.assertIn("ARM64", reason)

  def test_data_artifact_checksum_is_enforced(self):
    with tempfile.TemporaryDirectory() as td:
      p = Path(td) / "chunk"
      p.write_bytes(b"payload")
      ok, reason = validate_artifact(p, expected_sha256="0" * 64, require_elf=False)
      self.assertFalse(ok)
      self.assertIn("checksum", reason)

  def test_manifest_records_data_files_separately(self):
    with tempfile.TemporaryDirectory() as td:
      dest = Path(td)
      elf = dest / "openpilot/common/libparams_c.so"
      elf.parent.mkdir(parents=True)
      elf.write_bytes(elf_bytes(183))
      pkl = dest / self.PKL
      pkl.parent.mkdir(parents=True)
      pkl.write_bytes(b"kernels")

      write_manifest(dest, "abc123", "def456", ["openpilot/common/libparams_c.so"], [self.PKL])
      manifest = read_manifest(dest / "MANIFEST")
      self.assertEqual(manifest["data_files"], self.PKL)
      self.assertNotIn(self.PKL, manifest["files"])
      self.assertIn(f"sha256.{self.PKL}", manifest)

  def test_manifest_omits_data_files_key_when_absent(self):
    with tempfile.TemporaryDirectory() as td:
      dest = Path(td)
      elf = dest / "openpilot/common/libparams_c.so"
      elf.parent.mkdir(parents=True)
      elf.write_bytes(elf_bytes(183))
      write_manifest(dest, "abc123", "def456", ["openpilot/common/libparams_c.so"])
      self.assertNotIn("data_files", read_manifest(dest / "MANIFEST"))

  def test_find_data_artifacts_matches_pkl_chunks(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      models = root / "openpilot/selfdrive/modeld/models"
      models.mkdir(parents=True)
      (models / "driving_tinygrad.pkl.chunk01of02").write_bytes(b"a")
      (models / "driving_tinygrad.pkl.chunk02of02").write_bytes(b"b")
      (models / "driving_tinygrad.pkl.chunkmanifest").write_bytes(b"2")
      (models / "driving_supercombo.onnx").write_bytes(b"ignored")

      found = release_lib.find_data_artifacts(root)
      self.assertEqual(len(found), 3)
      self.assertTrue(all("driving_tinygrad.pkl" in f for f in found))
      self.assertFalse(any("onnx" in f for f in found))


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

  def _populate_prebuilt(self, worktree: Path, machine: int = 183, with_data: bool = True) -> None:
    prebuilt_root = worktree / "release/prebuilt/arm64"
    for rel in ARTIFACT_PATHS:
      src = prebuilt_root / rel
      src.parent.mkdir(parents=True, exist_ok=True)
      src.write_bytes(elf_bytes(machine))
    data_files: list[str] = []
    if with_data:
      pkl = prebuilt_root / "openpilot/selfdrive/modeld/models/driving_tinygrad.pkl.chunk01of02"
      pkl.parent.mkdir(parents=True, exist_ok=True)
      pkl.write_bytes(b"pickled tinygrad kernels")
      data_files = release_lib.find_data_artifacts(prebuilt_root)
    native_hash = compute_native_hash(worktree, "HEAD")
    write_manifest(prebuilt_root, "abc123", native_hash, list(ARTIFACT_PATHS), data_files)

  def test_overlay_ships_data_artifacts(self):
    """The model pkl must land in the worktree alongside the native ELFs."""
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree, with_data=True)
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertTrue(ok, reason)
      pkl = worktree / "openpilot/selfdrive/modeld/models/driving_tinygrad.pkl.chunk01of02"
      self.assertTrue(pkl.exists(), "model pkl chunk was not overlaid")
      self.assertEqual(pkl.read_bytes(), b"pickled tinygrad kernels")

  def test_overlay_fails_when_model_pkl_absent(self):
    """Shipping without the built-in pkl leaves modeld with no fallback."""
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree, with_data=False)
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertFalse(ok)
      self.assertIn("driving model pkl", reason)
      self.assertFalse((worktree / "prebuilt").exists())

  def test_overlay_fails_on_corrupt_data_artifact(self):
    with tempfile.TemporaryDirectory() as td:
      repo = self._make_repo(Path(td))
      worktree = self._make_worktree(Path(td), repo)
      self._populate_prebuilt(worktree, with_data=True)
      pkl = worktree / "release/prebuilt/arm64/openpilot/selfdrive/modeld/models/driving_tinygrad.pkl.chunk01of02"
      pkl.write_bytes(b"corrupted")
      ok, reason = overlay_prebuilt(repo, worktree)
      self.assertFalse(ok)
      self.assertIn("checksum", reason)
      self.assertFalse((worktree / "prebuilt").exists())

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
