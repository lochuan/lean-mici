"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

import hashlib

from openpilot.sunnypilot import get_file_hash
from openpilot.sunnypilot.models.default_model import MODEL_HASH_PATH, SUPERCOMBO_ONNX_PATH, _read_model_name_fields
from openpilot.common.test import OpenpilotTestCase


class TestDefaultModel(OpenpilotTestCase):
  def test_compare_onnx_hashes(self):
    fields = _read_model_name_fields()
    supercombo_hash = get_file_hash(SUPERCOMBO_ONNX_PATH)
    fingerprint = f"{supercombo_hash}:{fields.get('DEFAULT_MODEL', '')}:{fields.get('DEFAULT_MODEL_REF', '')}"
    combined_hash = hashlib.sha256(fingerprint.encode()).hexdigest()

    with open(MODEL_HASH_PATH) as f:
      current_hash = f.read().strip()

    assert combined_hash == current_hash, "Run openpilot/sunnypilot/models/default_model.py to update the default model name and hash"
