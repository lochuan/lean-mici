#!/usr/bin/env python3
"""Shared helpers for building, harvesting, and validating lean releases.

This module owns the release artifact model. The shell scripts in this
directory orchestrate Git, SSH, SCons, and OrbStack; this module decides
which files may ship, how they are hashed, and when ``prebuilt`` is valid.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path


# Every native runtime artifact that must be built on comma hardware.
# These paths are relative to the repository root.
ARTIFACT_PATHS: tuple[str, ...] = (
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
)

# Build inputs that can invalidate the native artifact set.
NATIVE_INPUT_PATHS: tuple[str, ...] = (
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
)

PREBUILT_DIR = Path("release/prebuilt/arm64")
MANIFEST_NAME = "MANIFEST"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    cmd,
    cwd=cwd,
    check=True,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
  )


def _run_with_stdin(
  cmd: list[str],
  cwd: Path | None,
  input: str,
) -> str:
  result = subprocess.run(
    cmd,
    cwd=cwd,
    input=input,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=True,
  )
  return result.stdout


def compute_native_hash(repo_root: Path, commit: str) -> str:
  """Hash the Git tree entries for every native build input."""
  tree = _run(
    ["git", "ls-tree", "-r", commit, "--", *NATIVE_INPUT_PATHS],
    cwd=repo_root,
  )
  hashed = _run_with_stdin(
    ["git", "hash-object", "--stdin"],
    cwd=repo_root,
    input=tree.stdout,
  )
  return hashed.strip()


def is_arm64_elf(path: Path) -> bool:
  """Return True when ``path`` is a 64-bit little-endian ARM ELF."""
  try:
    with path.open("rb") as f:
      header = f.read(20)
  except OSError:
    return False
  if len(header) < 20:
    return False
  if header[:4] != b"\x7fELF":
    return False
  if header[4] != 2:  # ELFCLASS64
    return False
  if header[5] != 1:  # ELFDATA2LSB
    return False
  machine = struct.unpack_from("<H", header, 18)[0]
  return machine == 183  # EM_AARCH64


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def write_manifest(dest: Path, source_commit: str, native_hash: str, files: list[str]) -> None:
  """Write ``PREBUILT_MANIFEST`` for artifacts already staged under ``dest``."""
  lines = [
    f"source_commit={source_commit}",
    f"native_hash={native_hash}",
    f"files={' '.join(files)}",
  ]
  for rel in files:
    path = dest / rel
    if not path.is_file():
      raise FileNotFoundError(f"missing artifact: {rel}")
    lines.append(f"sha256.{rel}={sha256_file(path)}")
  (dest / MANIFEST_NAME).write_text("\n".join(lines) + "\n")


def read_manifest(path: Path) -> dict[str, str]:
  """Parse a ``PREBUILT_MANIFEST`` file into a dictionary."""
  manifest: dict[str, str] = {}
  for line in path.read_text().splitlines():
    if not line or line.startswith("#"):
      continue
    if "=" not in line:
      raise ValueError(f"invalid manifest line: {line}")
    key, value = line.split("=", 1)
    manifest[key] = value
  return manifest


def validate_artifact(path: Path, expected_sha256: str | None = None) -> tuple[bool, str]:
  """Validate one harvested native artifact."""
  if not path.is_file():
    return False, f"missing artifact: {path}"
  if not is_arm64_elf(path):
    return False, f"not an ARM64 ELF: {path}"
  if expected_sha256 is not None:
    actual = sha256_file(path)
    if actual != expected_sha256:
      return False, f"checksum mismatch: {path}"
  return True, "ok"


def overlay_prebuilt(repo_root: Path, worktree: Path) -> tuple[bool, str]:
  """Validate and overlay device-built artifacts into a release worktree.

  Returns ``(True, "ok")`` when the release may ship with ``prebuilt``.
  Any validation failure returns ``(False, reason)`` and leaves the worktree
  without a ``prebuilt`` marker or copied native artifacts.
  """
  prebuilt_root = worktree / PREBUILT_DIR
  manifest_path = prebuilt_root / MANIFEST_NAME
  if not manifest_path.is_file():
    return False, f"missing manifest: {manifest_path}"

  try:
    manifest = read_manifest(manifest_path)
  except (OSError, ValueError) as exc:
    return False, f"invalid manifest: {exc}"

  current_hash = compute_native_hash(repo_root, "HEAD")
  if manifest.get("native_hash") != current_hash:
    return False, (
      "native hash mismatch: "
      f"manifest={manifest.get('native_hash')} current={current_hash}"
    )

  files = manifest.get("files", "").split()
  if not files:
    return False, "manifest has no artifact files"

  staged: list[tuple[Path, Path]] = []
  for rel in files:
    src = prebuilt_root / rel
    expected = manifest.get(f"sha256.{rel}")
    ok, reason = validate_artifact(src, expected)
    if not ok:
      return False, reason
    staged.append((src, worktree / rel))

  for src, dst in staged:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

  (worktree / "prebuilt").touch()
  shutil.rmtree(worktree / "release/prebuilt")
  return True, "ok"


def _repo_root() -> Path:
  result = _run(["git", "rev-parse", "--show-toplevel"])
  return Path(result.stdout.strip())


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)

  hash_parser = sub.add_parser("hash", help="print the native input hash")
  hash_parser.add_argument("commit", nargs="?", default="HEAD")

  sub.add_parser("artifact-paths", help="print native artifact paths")
  sub.add_parser("validate-artifacts", help="validate staged prebuilt artifacts")

  manifest_parser = sub.add_parser(
    "write-manifest",
    help="write PREBUILT_MANIFEST for staged artifacts",
  )
  manifest_parser.add_argument("source_commit")
  manifest_parser.add_argument("native_hash")

  overlay_parser = sub.add_parser(
    "overlay",
    help="validate and overlay prebuilt artifacts into a release worktree",
  )
  overlay_parser.add_argument("worktree")

  args = parser.parse_args()
  repo_root = _repo_root()

  if args.command == "hash":
    print(compute_native_hash(repo_root, args.commit))
    return 0

  if args.command == "artifact-paths":
    for rel in ARTIFACT_PATHS:
      print(rel)
    return 0

  if args.command == "validate-artifacts":
    prebuilt_root = repo_root / PREBUILT_DIR
    manifest_path = prebuilt_root / MANIFEST_NAME
    try:
      manifest = read_manifest(manifest_path)
    except (OSError, ValueError) as exc:
      print(f"invalid manifest: {exc}", file=sys.stderr)
      return 1
    files = manifest.get("files", "").split()
    if not files:
      print("manifest has no artifact files", file=sys.stderr)
      return 1
    failed = False
    for rel in files:
      ok, reason = validate_artifact(
        prebuilt_root / rel,
        manifest.get(f"sha256.{rel}"),
      )
      if not ok:
        print(reason, file=sys.stderr)
        failed = True
    return 1 if failed else 0

  if args.command == "write-manifest":
    write_manifest(
      repo_root / PREBUILT_DIR,
      args.source_commit,
      args.native_hash,
      list(ARTIFACT_PATHS),
    )
    return 0

  if args.command == "overlay":
    ok, reason = overlay_prebuilt(repo_root, Path(args.worktree))
    if not ok:
      print(reason, file=sys.stderr)
      return 1
    print("prebuilt shipped")
    return 0

  raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
  raise SystemExit(main())
