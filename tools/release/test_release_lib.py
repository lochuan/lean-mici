#!/usr/bin/env python3
"""Unit tests for tools/release/release_lib.py."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent / "release_lib.py"
spec = importlib.util.spec_from_file_location("release_lib", MODULE_PATH)
release_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_lib)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _init_repo_with_empty_commit(repo: Path) -> str:
  repo.mkdir(parents=True)
  for cmd in (
    ["git", "init", "-q", "-b", "main"],
    ["git", "config", "user.email", "t@example.com"],
    ["git", "config", "user.name", "t"],
    ["git", "commit", "-q", "--allow-empty", "-m", "init"],
  ):
    subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
  return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                        capture_output=True, text=True).stdout.strip()


def _source_repo_with_tinygrad_gitlink(td: str) -> tuple[Path, str]:
  """Source repo (lean-master shape) whose HEAD tracks tinygrad_repo as a gitlink; returns (repo, pinned sha)."""
  tinygrad_sha = _init_repo_with_empty_commit(Path(td) / "tinygrad")
  source = Path(td) / "source"
  _init_repo_with_empty_commit(source)
  for cmd in (
    ["git", "update-index", "--add", "--cacheinfo", f"160000,{tinygrad_sha},tinygrad_repo"],
    ["git", "commit", "-q", "-m", "gitlink"],
  ):
    subprocess.run(cmd, cwd=source, check=True, capture_output=True)
  return source, tinygrad_sha


class TestTinygradPin(unittest.TestCase):
  """发布树剥了 tinygrad_repo/.git：pin 取自源仓库的 gitlink（ls-tree），
  写进 stage，运行时 get_tinygrad_ref 靠它解析（模型选择器门控的输入）。"""

  def test_stamp_from_gitlink(self):
    with tempfile.TemporaryDirectory() as td:
      source, tinygrad_sha = _source_repo_with_tinygrad_gitlink(td)
      stage = Path(td) / "stage"
      self.assertEqual(release_lib.stamp_tinygrad_pin(stage, source), tinygrad_sha)
      self.assertEqual(release_lib.read_stage_tinygrad_pin(stage), tinygrad_sha)

  def test_stamp_source_without_gitlink_raises(self):
    with tempfile.TemporaryDirectory() as td:
      source = Path(td) / "source"
      _init_repo_with_empty_commit(source)
      with self.assertRaises(ValueError):
        release_lib.stamp_tinygrad_pin(Path(td) / "stage", source)

  def test_read_pin_absent(self):
    with tempfile.TemporaryDirectory() as td:
      self.assertIsNone(release_lib.read_stage_tinygrad_pin(Path(td)))

  def test_read_pin_malformed(self):
    with tempfile.TemporaryDirectory() as td:
      stage = Path(td)
      (stage / "tinygrad_repo").mkdir()
      (stage / "tinygrad_repo" / release_lib.TINYGRAD_PIN_FILE).write_text("zzz")
      self.assertIsNone(release_lib.read_stage_tinygrad_pin(stage))

  def test_stamped_pin_matches_runtime_resolution(self):
    sys.path.insert(0, str(REPO_ROOT))
    from openpilot.sunnypilot.models import tinygrad_ref
    with tempfile.TemporaryDirectory() as td:
      source, tinygrad_sha = _source_repo_with_tinygrad_gitlink(td)
      stage = Path(td) / "stage"
      release_lib.stamp_tinygrad_pin(stage, source)
      with mock.patch.object(tinygrad_ref, "BASEDIR", str(stage)):
        self.assertEqual(tinygrad_ref.get_tinygrad_ref(), tinygrad_sha)


class TestCheckStage(unittest.TestCase):
  """stage 树发布门禁：tinygrad pin 可解析 + 无超过 GitHub 单文件上限的文件。"""

  def _stamped_stage(self, td: str) -> Path:
    source, _ = _source_repo_with_tinygrad_gitlink(td)
    stage = Path(td) / "stage"
    release_lib.stamp_tinygrad_pin(stage, source)
    return stage

  def test_clean_stage_passes(self):
    with tempfile.TemporaryDirectory() as td:
      stage = self._stamped_stage(td)
      (stage / "small.bin").write_bytes(b"x" * 10)
      self.assertEqual(release_lib.check_stage(stage), [])

  def test_missing_pin_fails(self):
    with tempfile.TemporaryDirectory() as td:
      problems = release_lib.check_stage(Path(td))
      self.assertTrue(any("tinygrad pin" in p for p in problems), problems)

  def test_oversized_file_fails(self):
    with tempfile.TemporaryDirectory() as td:
      stage = self._stamped_stage(td)
      big = stage / "openpilot" / "big.pkl"
      big.parent.mkdir()
      with open(big, "wb") as f:
        f.truncate(release_lib.MAX_FILE_BYTES + 1)
      problems = release_lib.check_stage(stage)
      self.assertEqual(len(problems), 1, problems)
      self.assertIn("openpilot/big.pkl", problems[0])

  def test_file_at_limit_passes(self):
    with tempfile.TemporaryDirectory() as td:
      stage = self._stamped_stage(td)
      with open(stage / "edge.bin", "wb") as f:
        f.truncate(release_lib.MAX_FILE_BYTES)
      self.assertEqual(release_lib.check_stage(stage), [])


class TestCli(unittest.TestCase):
  """device_release.sh 经 CLI 调用：stamp 后 check 通过，返回码即门禁结果。"""

  def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(MODULE_PATH), *args], capture_output=True, text=True)

  def test_stamp_then_check(self):
    with tempfile.TemporaryDirectory() as td:
      source, tinygrad_sha = _source_repo_with_tinygrad_gitlink(td)
      stage = Path(td) / "stage"
      stamp = self._cli("stamp-tinygrad-pin", str(stage), str(source))
      self.assertEqual(stamp.returncode, 0, stamp.stderr)
      self.assertIn(tinygrad_sha[:7], stamp.stdout)
      check = self._cli("check-stage", str(stage))
      self.assertEqual(check.returncode, 0, check.stderr)

  def test_check_stage_rejects(self):
    with tempfile.TemporaryDirectory() as td:
      check = self._cli("check-stage", td)
      self.assertEqual(check.returncode, 1)
      self.assertIn("tinygrad pin", check.stderr)


if __name__ == "__main__":
  unittest.main()
