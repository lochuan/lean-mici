#!/usr/bin/env python3
"""Tests for modeld's driving-pkl resolution.

An interrupted model download leaves a .chunkmanifest with no chunks. Treating
that as a usable artifact skipped _find_driving_pkl's fallback to the built-in
pkl, so open_file_chunked raised FileNotFoundError, modeld died and openpilot
refused to start -- on a device that had a perfectly good built-in model.
Observed on-device with 77 manifest stubs and zero chunks in
/data/media/0/models.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpilot.common.file_chunker import get_chunk_name, get_manifest_path
from openpilot.selfdrive.modeld.modeld import _pkl_exists


class TestPklExists(unittest.TestCase):
  def setUp(self):
    self._td = tempfile.TemporaryDirectory()
    self.root = Path(self._td.name)
    self.addCleanup(self._td.cleanup)

  def _write_set(self, name: str, num_chunks: int, present: int | None = None) -> str:
    """Write a chunk set; ``present`` chunks actually exist (default: all)."""
    base = str(self.root / name)
    Path(get_manifest_path(base)).write_text(str(num_chunks))
    for i in range(num_chunks if present is None else present):
      Path(get_chunk_name(base, i, num_chunks)).write_bytes(b"x")
    return base

  def test_whole_file(self):
    p = self.root / "model.pkl"
    p.write_bytes(b"kernels")
    self.assertTrue(_pkl_exists(str(p)))

  def test_absent(self):
    self.assertFalse(_pkl_exists(str(self.root / "nope.pkl")))

  def test_complete_chunk_set(self):
    self.assertTrue(_pkl_exists(self._write_set("model.pkl", 3)))

  def test_manifest_with_no_chunks_at_all(self):
    """The on-device failure: a lone manifest stub must not count."""
    self.assertFalse(_pkl_exists(self._write_set("model.pkl", 2, present=0)))

  def test_partially_downloaded_chunk_set(self):
    self.assertFalse(_pkl_exists(self._write_set("model.pkl", 3, present=2)))

  def test_corrupt_manifest(self):
    base = str(self.root / "model.pkl")
    Path(get_manifest_path(base)).write_text("not a number")
    self.assertFalse(_pkl_exists(base))

  def test_zero_chunk_manifest(self):
    base = str(self.root / "model.pkl")
    Path(get_manifest_path(base)).write_text("0")
    self.assertFalse(_pkl_exists(base))

  def test_empty_chunks_still_count(self):
    """chunk_file pads a set with empty files when the estimate overshoots."""
    base = str(self.root / "model.pkl")
    Path(get_manifest_path(base)).write_text("3")
    Path(get_chunk_name(base, 0, 3)).write_bytes(b"data")
    Path(get_chunk_name(base, 1, 3)).write_bytes(b"data")
    Path(get_chunk_name(base, 2, 3)).write_bytes(b"")  # legitimately empty
    self.assertTrue(_pkl_exists(base))


class TestFindDrivingPkl(unittest.TestCase):
  """A selected-but-undownloaded bundle must fall back to the built-in pkl."""

  def setUp(self):
    self._td = tempfile.TemporaryDirectory()
    self.root = Path(self._td.name)
    self.addCleanup(self._td.cleanup)

  def test_falls_back_when_bundle_chunks_are_missing(self):
    from openpilot.selfdrive.modeld import modeld

    model_root = self.root / "models"
    model_root.mkdir()
    # bundle artifact: manifest stub only, as an interrupted download leaves it
    bundle_base = model_root / "driving_custom_tinygrad.pkl"
    Path(get_manifest_path(str(bundle_base))).write_text("2")

    builtin = self.root / "driving_tinygrad.pkl"
    builtin.write_bytes(b"builtin kernels")

    bundle = mock.Mock()
    bundle.models = [mock.Mock(artifact=mock.Mock(fileName="driving_custom_tinygrad.pkl"))]

    with mock.patch.object(modeld, "modeld_pkl_path", return_value=builtin), \
         mock.patch("openpilot.common.hardware.hw.Paths.model_root", return_value=str(model_root)), \
         mock.patch.dict(os.environ, {}, clear=False):
      os.environ.pop("COMBINED_MODEL_PKL", None)
      path, used_bundle = modeld._find_driving_pkl(bundle)

    self.assertEqual(path, str(builtin))
    self.assertIsNone(used_bundle, "falling back must clear the bundle")


if __name__ == "__main__":
  unittest.main()
