import os
import tempfile
from unittest import mock

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.models import pin


class TestPklPin(OpenpilotTestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.pkl = os.path.join(self.tmp.name, "driving_test.pkl")
    with open(self.pkl, "wb") as f:
      f.write(b"pkl")

  def test_no_sidecar_is_unknown(self):
    with mock.patch.object(pin, "device_tinygrad_ref", return_value="aaaa"):
      assert pin.pkl_pin_compatible(self.pkl) is None

  def test_matching_pin_is_compatible(self):
    pin.write_pkl_pin(self.pkl, "aaaa")
    with mock.patch.object(pin, "device_tinygrad_ref", return_value="aaaa"):
      assert pin.pkl_pin_compatible(self.pkl) is True

  def test_mismatched_pin_is_incompatible(self):
    pin.write_pkl_pin(self.pkl, "eeee")
    with mock.patch.object(pin, "device_tinygrad_ref", return_value="aaaa"):
      assert pin.pkl_pin_compatible(self.pkl) is False

  def test_unresolvable_device_ref_is_unknown(self):
    pin.write_pkl_pin(self.pkl, "aaaa")
    with mock.patch.object(pin, "device_tinygrad_ref", return_value=None):
      assert pin.pkl_pin_compatible(self.pkl) is None

  def test_device_ref_reads_submodule_head(self):
    # the tree's tinygrad_repo is a real checkout (or gitlink); resolution must
    # return a 40-char sha in this repo, None elsewhere
    ref = pin.device_tinygrad_ref()
    assert ref is None or (len(ref) == 40 and all(c in "0123456789abcdef" for c in ref))
