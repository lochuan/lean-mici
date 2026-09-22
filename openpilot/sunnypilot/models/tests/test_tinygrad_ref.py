import os
import tempfile
from unittest import mock

import requests

from openpilot.sunnypilot.models import tinygrad_ref
from openpilot.sunnypilot.models.tinygrad_ref import get_tinygrad_ref, TINYGRAD_PIN_FILE
from openpilot.sunnypilot.models.fetcher import ModelFetcher
from openpilot.common.test import OpenpilotTestCase

def fetch_tinygrad_ref():
  response = requests.get(ModelFetcher.MODEL_URL, timeout=10)
  response.raise_for_status()
  json_data = response.json()
  return json_data.get("tinygrad_ref")


class TestTinygradRef(OpenpilotTestCase):
  def test_tinygrad_ref(self):
    current_ref = get_tinygrad_ref()
    remote_ref = fetch_tinygrad_ref()
    assert remote_ref == current_ref, (
      f"""tinygrad_repo ref does not match remote tinygrad_ref of current compiled driving models json.
    Current: {current_ref}
    Remote: {remote_ref}
    Please run build-all workflow to update models."""
    )
    print("tinygrad_repo ref matches current compiled driving models json ref.")


SHA = "a" * 40
SHA2 = "b" * 40


class TestFlatTreePinResolution(OpenpilotTestCase):
  """Published flat trees strip tinygrad_repo/.git; the pin must still resolve
  via the release-stamped TINYGRAD_PIN file (model selector gating input)."""

  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.basedir = self.tmp.name
    os.makedirs(os.path.join(self.basedir, "tinygrad_repo"))

  def _resolve(self):
    with mock.patch.object(tinygrad_ref, "BASEDIR", self.basedir):
      return get_tinygrad_ref()

  def test_git_dir_head(self):
    os.makedirs(os.path.join(self.basedir, "tinygrad_repo", ".git"))
    with open(os.path.join(self.basedir, "tinygrad_repo", ".git", "HEAD"), "w") as f:
      f.write(SHA)
    assert self._resolve() == SHA

  def test_git_dir_ref_pointer(self):
    git = os.path.join(self.basedir, "tinygrad_repo", ".git")
    os.makedirs(os.path.join(git, "refs", "heads"))
    with open(os.path.join(git, "HEAD"), "w") as f:
      f.write("ref: refs/heads/master")
    with open(os.path.join(git, "refs", "heads", "master"), "w") as f:
      f.write(SHA)
    assert self._resolve() == SHA

  def test_git_pointer_file(self):
    os.makedirs(os.path.join(self.basedir, "realgit"))
    with open(os.path.join(self.basedir, "realgit", "HEAD"), "w") as f:
      f.write(SHA)
    with open(os.path.join(self.basedir, "tinygrad_repo", ".git"), "w") as f:
      f.write("gitdir: ../realgit")
    assert self._resolve() == SHA

  def test_flat_tree_pin_file(self):
    with open(os.path.join(self.basedir, "tinygrad_repo", TINYGRAD_PIN_FILE), "w") as f:
      f.write(SHA + "\n")
    assert self._resolve() == SHA

  def test_flat_tree_no_git_no_pin(self):
    assert self._resolve() is None

  def test_broken_git_falls_back_to_pin(self):
    os.makedirs(os.path.join(self.basedir, "tinygrad_repo", ".git"))
    with open(os.path.join(self.basedir, "tinygrad_repo", TINYGRAD_PIN_FILE), "w") as f:
      f.write(SHA2)
    assert self._resolve() == SHA2

  def test_blank_pin_file_is_none(self):
    with open(os.path.join(self.basedir, "tinygrad_repo", TINYGRAD_PIN_FILE), "w") as f:
      f.write("  \n")
    assert self._resolve() is None
