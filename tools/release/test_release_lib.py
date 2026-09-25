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
      "msgq_repo/msgq/ipc_pyx.so",
      "msgq_repo/msgq/visionipc/visionipc_pyx.so",
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
      "openpilot/system/loggerd/bootlog",
      "openpilot/system/loggerd/loggerd",
      "rednose_repo/rednose/helpers/ekf_sym_pyx.so",
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
    change to the model, the compiler, or tinygrad has to force a source-only release.
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

  def test_find_data_artifacts_ignores_a_stale_chunk_set(self):
    """Only the set the chunkmanifest claims counts.

    chunk_file names chunks chunkNNofMM where MM is an estimate, so a pkl whose
    size crosses a 45MB boundary leaves the previous set on disk. Globbing
    chunk* shipped both and listed the dead one in the manifest.
    """
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      models = root / "openpilot/selfdrive/modeld/models"
      models.mkdir(parents=True)
      # current set, per the manifest
      (models / "driving_tinygrad.pkl.chunk01of02").write_bytes(b"a")
      (models / "driving_tinygrad.pkl.chunk02of02").write_bytes(b"b")
      (models / "driving_tinygrad.pkl.chunkmanifest").write_bytes(b"2")
      # leftovers from an earlier build that estimated three chunks
      (models / "driving_tinygrad.pkl.chunk01of03").write_bytes(b"stale")
      (models / "driving_tinygrad.pkl.chunk02of03").write_bytes(b"stale")
      (models / "driving_tinygrad.pkl.chunk03of03").write_bytes(b"")

      found = release_lib.find_data_artifacts(root)
      self.assertEqual(len(found), 3, found)
      self.assertFalse(any("of03" in f for f in found), found)

      stale = release_lib.stale_data_artifacts(root)
      self.assertEqual(len(stale), 3, stale)
      self.assertTrue(all("of03" in f for f in stale), stale)

  def test_find_data_artifacts_needs_a_chunkmanifest(self):
    """Chunks with no manifest are unreadable, so they are not artifacts."""
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      models = root / "openpilot/selfdrive/modeld/models"
      models.mkdir(parents=True)
      (models / "driving_tinygrad.pkl.chunk01of02").write_bytes(b"orphan")
      self.assertEqual(release_lib.find_data_artifacts(root), [])


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
      models = prebuilt_root / "openpilot/selfdrive/modeld/models"
      models.mkdir(parents=True, exist_ok=True)
      # A real chunk set always carries its chunkmanifest -- chunk_file writes
      # it, and open_file_chunked needs it to know how many chunks to read.
      (models / "driving_tinygrad.pkl.chunk01of02").write_bytes(b"pickled tinygrad kernels")
      (models / "driving_tinygrad.pkl.chunk02of02").write_bytes(b"more kernels")
      (models / "driving_tinygrad.pkl.chunkmanifest").write_bytes(b"2")
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
      # The marker is earned at runtime by the device, never shipped.
      self.assertFalse((worktree / "prebuilt").exists())
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


def _git_commit(repo: Path) -> str:
  for cmd in (
    ["git", "init", "-q", "-b", "main"],
    ["git", "config", "user.email", "t@example.com"],
    ["git", "config", "user.name", "t"],
    ["git", "commit", "--allow-empty", "-m", "init"],
  ):
    subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
  return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                        capture_output=True, text=True).stdout.strip()


def _git_parent_with_gitlink(parent: Path, sha: str) -> str:
  """Parent repo (lean-master shape) tracking tinygrad_repo as a gitlink."""
  for cmd in (
    ["git", "init", "-q", "-b", "main"],
    ["git", "config", "user.email", "t@example.com"],
    ["git", "config", "user.name", "t"],
    ["git", "commit", "--allow-empty", "-m", "init"],
    ["git", "update-index", "--add", "--cacheinfo", f"160000,{sha},tinygrad_repo"],
    ["git", "commit", "-m", "gitlink"],
  ):
    subprocess.run(cmd, cwd=parent, check=True, capture_output=True)
  return subprocess.run(["git", "rev-parse", "HEAD"], cwd=parent, check=True,
                        capture_output=True, text=True).stdout.strip()


class TestTinygradPinStage(unittest.TestCase):
  """扁平树剥离 tinygrad_repo/.git：发布时必须 stamp pin，且 stage 能自行解析
  （模型选择器四层门控的输入，device_release.sh 的发布门禁依赖此函数）。

  pin 来源 = lean-master 的 gitlink（`ls-tree`），不是 tinygrad_repo 自身的
  rev-parse：设备树可能已是扁平消费者（tinygrad_repo 无 .git，rev-parse
  会穿透到父仓库返回 release commit，是垃圾 pin）。"""

  def _stage_from_gitlink(self, td: str) -> tuple[Path, str]:
    tiny_src = Path(td) / "tiny_src"
    tiny_src.mkdir()
    tiny_commit = _git_commit(tiny_src)
    parent = Path(td) / "parent"
    parent.mkdir()
    _git_parent_with_gitlink(parent, tiny_commit)
    stage = Path(td) / "stage"
    (stage / "tinygrad_repo").mkdir(parents=True)
    stamped = release_lib.stamp_tinygrad_pin(stage, parent, treeish="HEAD")
    return stage, stamped

  def test_stage_pin_roundtrip(self):
    with tempfile.TemporaryDirectory() as td:
      stage, stamped = self._stage_from_gitlink(td)
      self.assertEqual(release_lib.stage_tinygrad_pin(stage), stamped)

  def test_stage_pin_absent(self):
    with tempfile.TemporaryDirectory() as td:
      self.assertIsNone(release_lib.stage_tinygrad_pin(Path(td)))

  def test_stage_pin_malformed(self):
    with tempfile.TemporaryDirectory() as td:
      stage = Path(td)
      (stage / "tinygrad_repo").mkdir()
      (stage / "tinygrad_repo" / release_lib.TINYGRAD_PIN_FILE).write_text("zzz")
      self.assertIsNone(release_lib.stage_tinygrad_pin(stage))

  def test_stage_pin_matches_runtime_resolution(self):
    # 发布门禁的语义保证：release_lib 的解析必须与运行时 get_tinygrad_ref 一致
    import sys
    from unittest import mock
    sys.path.insert(0, str(REPO_ROOT))
    from openpilot.sunnypilot.models import tinygrad_ref
    with tempfile.TemporaryDirectory() as td:
      stage, stamped = self._stage_from_gitlink(td)
      with mock.patch.object(tinygrad_ref, "BASEDIR", str(stage)):
        self.assertEqual(tinygrad_ref.get_tinygrad_ref(), stamped)
      self.assertEqual(release_lib.stage_tinygrad_pin(stage), stamped)


class TestGitlinkPinStamp(unittest.TestCase):
  """扁平消费者设备上 tinygrad_repo 没有 .git：pin 必须取自 lean-master
  的 gitlink（ls-tree），绝不能用 rev-parse 穿透到父仓库。"""

  def test_stamp_from_gitlink(self):
    with tempfile.TemporaryDirectory() as td:
      tiny_src = Path(td) / "tiny_src"
      tiny_src.mkdir()
      tiny_commit = _git_commit(tiny_src)
      parent = Path(td) / "parent"
      parent.mkdir()
      _git_parent_with_gitlink(parent, tiny_commit)
      stage = Path(td) / "stage"
      (stage / "tinygrad_repo").mkdir(parents=True)
      stamped = release_lib.stamp_tinygrad_pin(
        stage, parent, treeish="HEAD")
      self.assertEqual(stamped, tiny_commit)
      self.assertEqual(release_lib.stage_tinygrad_pin(stage), tiny_commit)

  def test_stamp_from_named_ref(self):
    with tempfile.TemporaryDirectory() as td:
      tiny_src = Path(td) / "t"
      tiny_src.mkdir()
      tiny_commit = _git_commit(tiny_src)
      parent = Path(td) / "parent"
      parent.mkdir()
      _git_parent_with_gitlink(parent, tiny_commit)
      subprocess.run(["git", "update-ref", "refs/remotes/origin/lean-master", "HEAD"],
                     cwd=parent, check=True, capture_output=True)
      stage = Path(td) / "stage"
      (stage / "tinygrad_repo").mkdir(parents=True)
      stamped = release_lib.stamp_tinygrad_pin(
        stage, parent, treeish="refs/remotes/origin/lean-master")
      self.assertEqual(stamped, tiny_commit)

  def test_stamp_parent_without_gitlink_raises(self):
    with tempfile.TemporaryDirectory() as td:
      parent = Path(td) / "parent"
      parent.mkdir()
      _git_commit(parent)
      with self.assertRaises(ValueError):
        release_lib.stamp_tinygrad_pin(Path(td) / "stage", parent, treeish="HEAD")


class TestFlatTreeEntries(unittest.TestCase):
  """扁平树运行时必需、lean-master 不跟踪的结构性条目：同步门禁（AM 白名单）
  必须放行它们，否则消费态设备发布会被自己的门禁卡死。"""

  def test_entries_present(self):
    self.assertIn("msgq", release_lib.FLAT_TREE_ENTRIES)
    self.assertIn("opendbc", release_lib.FLAT_TREE_ENTRIES)
    self.assertIn("rednose", release_lib.FLAT_TREE_ENTRIES)
    self.assertIn("tinygrad", release_lib.FLAT_TREE_ENTRIES)  # 运行时 import 的 symlink
    self.assertIn("prebuilt", release_lib.FLAT_TREE_ENTRIES)  # 发布标记
    self.assertIn(".overlay_init", release_lib.FLAT_TREE_ENTRIES)


if __name__ == "__main__":
  unittest.main()


class TestParamsKeyGate(unittest.TestCase):
  """发布门禁：params_keys.h 注册的每个键必须已编译进 libparams_c.so。

  2026-09-24 事故：776b4d5a3 只改了头文件，设备 prebuilt 标记 + 扁平树发布
  流程没有 scons 步骤，libparams_c.so 一直停留在 09-18 的键表 —— lanlink
  上 5 个避让/变道设置全部显示"此版本固件未提供该设置"。header 与编译产物
  的差集就是这类事故的门禁。
  """

  HEADER_SAMPLE = """
  // comment with {"NotAKey", {PERSISTENT, BOOL}} inside
  {"AvoidanceEnabled", {PERSISTENT | BACKUP, BOOL, "0"}},
  {"AvoidanceSideMargin", {PERSISTENT, FLOAT, "0.3"}},
  {"AvoidanceEgoHalfWidth", {PERSISTENT, FLOAT, "0.9"}},  // trailing comment
  {"AvoidanceLaneProbMin", {PERSISTENT, FLOAT, "0.6"}},
  {"AvoidanceLaneStdMax", {PERSISTENT, FLOAT, "0.3"}},
  """

  def test_parses_every_key_line_ignoring_comments(self):
    keys = release_lib.params_keys_from_header(self.HEADER_SAMPLE)
    self.assertEqual(keys, [
      "AvoidanceEnabled", "AvoidanceSideMargin", "AvoidanceEgoHalfWidth",
      "AvoidanceLaneProbMin", "AvoidanceLaneStdMax",
    ])

  def test_real_header_parses_to_known_keys(self):
    """Regex 对真实头文件不能失手：已知键必须解析出来，且全表非空。"""
    header = REPO_ROOT / "openpilot/common/params_keys.h"
    keys = release_lib.params_keys_from_header(header.read_text())
    self.assertIn("AvoidanceEnabled", keys)
    self.assertIn("AvoidanceSideMargin", keys)
    self.assertIn("LaneChangeNearZone", keys)
    self.assertGreater(len(keys), 100)

  def test_missing_keys_is_header_minus_compiled(self):
    header = ["A", "B", "C"]
    self.assertEqual(release_lib.missing_compiled_keys(header, {"B"}), ["A", "C"])

  def test_missing_keys_empty_when_compiled_superset(self):
    self.assertEqual(release_lib.missing_compiled_keys(["A"], {"A", "Z"}), [])

  def test_missing_keys_accepts_bytes_compiled_keys(self):
    """Params.all_keys() 返回 ctypes bytes——键名必须归一化后比对。"""
    self.assertEqual(release_lib.missing_compiled_keys(["A", "B"], [b"A", b"Z"]), ["B"])
    self.assertEqual(release_lib.missing_compiled_keys(["A"], (b"A",)), [])

  def test_missing_keys_empty_for_empty_header(self):
    self.assertEqual(release_lib.missing_compiled_keys([], {"A"}), [])


class TestInputsFingerprint(unittest.TestCase):
  """pkl 缓存判据:输入(文件内容+附加串)不变则指纹不变。

  2026-09-25 议定:pkl 只依赖 tinygrad pin、onnx、编译脚本与编译参数 ——
  三者不变时重编纯浪费(每次发布 ~20 分钟)。指纹就是"要不要重编"的唯一依据。
  """

  def _file(self, td: Path, name: str, content: bytes) -> Path:
    p = td / name
    p.write_bytes(content)
    return p

  def test_same_inputs_same_fingerprint(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      a = self._file(root, "a.bin", b"aaa")
      b = self._file(root, "b.bin", b"bbb")
      f1 = release_lib.inputs_fingerprint([a, b], extras=["x"])
      f2 = release_lib.inputs_fingerprint([b, a], extras=["x"])   # 顺序无关
      self.assertEqual(f1, f2)

  def test_content_or_extras_change_changes_fingerprint(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      a = self._file(root, "a.bin", b"aaa")
      base = release_lib.inputs_fingerprint([a], extras=["x"])
      self.assertNotEqual(base, release_lib.inputs_fingerprint([a], extras=["y"]))
      a.write_bytes(b"aaa2")
      self.assertNotEqual(base, release_lib.inputs_fingerprint([a], extras=["x"]))

  def test_missing_input_raises(self):
    with self.assertRaises(FileNotFoundError):
      release_lib.inputs_fingerprint([Path("/nonexistent/nope.bin")])


class TestSchemaStamp(unittest.TestCase):
  """gen/cpp 与 .capnp schema 一致性门禁:params 键表事故的同类缺口。

  设备无 capnpc 工具链,SKIP_CAPNP_REGEN=1 编译的是 checked-in gen/cpp ——
  schema 改了忘了在 Mac 侧重生成时,设备会静默编译旧生成物。生成时盖
  .schema.sha 指纹,发布时校验,漂移即拒发。
  """

  def _schema(self, td: Path, name: str, content: str) -> Path:
    p = td / name
    p.write_text(content)
    return p

  def test_stamp_then_check_roundtrip(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      gen = root / "gen"
      gen.mkdir()
      s1 = self._schema(root, "a.capnp", "struct A {}")
      s2 = self._schema(root, "b.capnp", "struct B {}")
      release_lib.stamp_schemas(gen, [s1, s2])
      ok, reason = release_lib.check_schema_stamp(gen, [s1, s2])
      self.assertTrue(ok, reason)

  def test_schema_change_after_stamp_fails(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      gen = root / "gen"
      gen.mkdir()
      s1 = self._schema(root, "a.capnp", "struct A {}")
      release_lib.stamp_schemas(gen, [s1])
      s1.write_text("struct A { x @0 :Float32; }")
      ok, reason = release_lib.check_schema_stamp(gen, [s1])
      self.assertFalse(ok)
      self.assertIn("mismatch", reason)

  def test_missing_stamp_fails(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      gen = root / "gen"
      gen.mkdir()
      s1 = self._schema(root, "a.capnp", "struct A {}")
      ok, reason = release_lib.check_schema_stamp(gen, [s1])
      self.assertFalse(ok)
      self.assertIn("missing", reason)


class TestSchemaRegistry(unittest.TestCase):
  """schema/构建输入清单的唯一登记表（fix/schema-registry ①）。

  同一个「cereal 有哪几个 schema」的事实此前登记三份（regen 脚本数组、
  device_release 门禁参数、SConscript 构建输入），漏改一处不报错——只是
  发布物悄悄缺件。登记表落 openpilot/cereal/schemas.py：SConscript 同包
  import，shell 经 release_lib.py schema-paths CLI 取（照 artifact-paths 先例）。
  这些断言测的是**接线**：消费方从登记表取数，防止有人在 shell 里手写回一份。
  """

  def test_registry_lists_exactly_the_four_schemas(self):
    from openpilot.cereal.schemas import SCHEMAS
    self.assertEqual(SCHEMAS, (
      "openpilot/cereal/log.capnp",
      "openpilot/cereal/deprecated.capnp",
      "openpilot/cereal/custom.capnp",
      "opendbc_repo/opendbc/car/car.capnp",
    ))

  def test_registry_entries_exist_in_tree(self):
    from openpilot.cereal.schemas import SCHEMAS
    for rel in SCHEMAS:
      self.assertTrue((REPO_ROOT / rel).is_file(), rel)

  def test_registry_entries_are_repo_relative(self):
    """规范形是仓库相对路径：$SRC 绝对形 / SCons '#' 锚形由消费方各自推导。"""
    from openpilot.cereal.schemas import SCHEMAS
    for rel in SCHEMAS:
      self.assertFalse(rel.startswith(("/", "#")), rel)

  def test_schema_paths_cli_matches_registry(self):
    import sys as _sys
    from openpilot.cereal.schemas import SCHEMAS
    out = subprocess.run(
      [_sys.executable, str(MODULE_PATH), "schema-paths"],
      capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    self.assertEqual(tuple(out.stdout.split()), SCHEMAS)

  def test_check_schema_stamp_defaults_to_registry(self):
    """缺省参数走登记表：发布门禁不再手抄 schema 清单。"""
    import sys as _sys
    from openpilot.cereal.schemas import SCHEMAS
    gen = REPO_ROOT / "openpilot/cereal/gen/cpp"
    ok_default = subprocess.run(
      [_sys.executable, str(MODULE_PATH), "check-schema-stamp", str(gen)],
      capture_output=True, text=True, cwd=REPO_ROOT,
    )
    ok_explicit = subprocess.run(
      [_sys.executable, str(MODULE_PATH), "check-schema-stamp", str(gen), *SCHEMAS],
      capture_output=True, text=True, cwd=REPO_ROOT,
    )
    self.assertEqual(ok_default.returncode, ok_explicit.returncode)
    self.assertEqual(ok_default.stdout, ok_explicit.stdout)

  def test_sconscript_consumes_registry(self):
    """SConscript 的构建输入必须从登记表推导——源码级接线断言
    （Q6a：消费方从登记表取数这个接线本身要有人看着）。"""
    src = (REPO_ROOT / "openpilot/cereal/SConscript").read_text()
    self.assertIn("from openpilot.cereal.schemas import", src)
    for name in ("log.capnp", "deprecated.capnp", "custom.capnp"):
      self.assertNotIn(f"'{name}'", src, f"SConscript 不得手写 schema 名 {name}")

  def test_regen_shell_pulls_schema_list_from_cli(self):
    sh = (REPO_ROOT / "tools/release/regen_cereal_gen.sh").read_text()
    self.assertIn("schema-paths", sh)
    self.assertNotIn("openpilot/cereal/log.capnp", sh,
                     "regen 脚本不得手写 schema 路径（要经 schema-paths 取）")


class TestCheckParamsKeys(unittest.TestCase):
  """params 键表门禁下沉为 check-params-keys（fix/schema-registry ② 捎带）。

  规则（params_keys_from_header/missing_compiled_keys）已在上方覆盖；
  这里测下沉后的接线：CLI 子命令做「读 header → 取编译表 → 比对 → 非零拒发」。
  """

  def test_check_params_keys_reports_missing_and_fails(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      hdr = root / "openpilot/common/params_keys.h"
      hdr.parent.mkdir(parents=True)
      hdr.write_text('{"Alpha", {PERSISTENT, BOOL}},\n  {"Beta", {PERSISTENT, BOOL}},\n')
      missing = release_lib.check_params_keys(root, {b"Alpha"})
      self.assertEqual(missing, ["Beta"])

  def test_check_params_keys_empty_when_all_compiled(self):
    with tempfile.TemporaryDirectory() as td:
      root = Path(td)
      hdr = root / "openpilot/common/params_keys.h"
      hdr.parent.mkdir(parents=True)
      hdr.write_text('{"Alpha", {PERSISTENT, BOOL}},\n')
      self.assertEqual(release_lib.check_params_keys(root, {b"Alpha"}), [])
