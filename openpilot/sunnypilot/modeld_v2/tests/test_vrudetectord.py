"""
Copyright (c) 2021-, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import os

from openpilot.common.test import OpenpilotTestCase

PKL = os.path.join(os.path.dirname(__file__), '../../../selfdrive/modeld/models/vru_detect_tinygrad.pkl')


class TestVRUDetectord(OpenpilotTestCase):
  def test_import(self):
    import openpilot.sunnypilot.modeld_v2.vrudetectord as mod
    self.assertTrue(callable(mod.main))

  def test_process_registered(self):
    from openpilot.system.manager.process_config import procs
    names = {p.name for p in procs}
    assert 'vrudetectord' in names

  def test_model_smoke(self):
    import pickle
    import numpy as np
    from openpilot.common.file_chunker import open_file_chunked
    from tinygrad.tensor import Tensor
    from openpilot.sunnypilot.modeld_v2.vru_decode import decode_detections
    if not (os.path.exists(PKL) or os.path.exists(PKL + '.chunkmanifest')):
      self.skipTest('vru_detect pkl not built')
    model_run = pickle.load(open_file_chunked(PKL))
    x = np.random.default_rng(0).random((1, 3, 192, 320), dtype=np.float32) * 0.5
    out = model_run(images=Tensor(x, device='NPY').realize()).numpy()
    assert out.shape == (1, 84, 1260) and np.isfinite(out).all()
    decode_detections(out, 192, 320)
