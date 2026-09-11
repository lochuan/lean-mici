"""Tests for split-model (vision + policy) support in modeld.

These cover the pure-python logic that can run without tinygrad device access:
MHP inference, queue/shape derivation, pkl resolution fallback, and the
big-model filter. Actual inference is verified on-device.
"""
import os
from unittest import mock

import numpy as np
import pytest

from openpilot.selfdrive.modeld.compile_modeld import (_detect_desire_key, _detect_vision_keys,
                                                       derive_frame_skip, get_policy_npy_shapes)
from openpilot.selfdrive.modeld.parse_model_outputs import Parser, _infer_mhp
from openpilot.selfdrive.modeld.constants import ModelConstants

# Real shapes, read off the shipped models.
SUPERCOMBO_SHAPES = {
  'img': (1, 12, 128, 256),
  'big_img': (1, 12, 128, 256),
  'features_buffer': (1, 24, 512),
  'desire_pulse': (1, 25, 8),
  'traffic_convention': (1, 2),
  'action_t': (1, 2),
}
SPLIT_VISION_SHAPES = {'img': (1, 12, 128, 256), 'big_img': (1, 12, 128, 256)}
SPLIT_POLICY_SHAPES = {
  'desire': (1, 25, 8),
  'traffic_convention': (1, 2),
  'lateral_control_params': (1, 2),
  'prev_desired_curv': (1, 25, 1),
  'features_buffer': (1, 25, 512),
}


class TestKeyDetection:
  def test_detects_supercombo_desire_key(self):
    assert _detect_desire_key(SUPERCOMBO_SHAPES) == 'desire_pulse'

  def test_detects_split_desire_key(self):
    assert _detect_desire_key(SPLIT_POLICY_SHAPES) == 'desire'

  def test_missing_desire_key_is_none(self):
    assert _detect_desire_key({'img': (1, 12, 128, 256)}) is None

  def test_detects_vision_keys(self):
    assert _detect_vision_keys(SPLIT_VISION_SHAPES) == ('img', 'big_img')


class TestFrameSkip:
  def test_skips_for_subsampled_buffer(self):
    assert derive_frame_skip(SPLIT_VISION_SHAPES, SPLIT_POLICY_SHAPES) == 4

  def test_no_skip_for_full_rate_buffer(self):
    assert derive_frame_skip({}, {'features_buffer': (1, 99, 512)}) == 1

  def test_no_skip_without_features_buffer(self):
    assert derive_frame_skip({}, {}) == 1


class TestPolicyNpyShapes:
  def test_supercombo_layout_is_unchanged(self):
    """Ordering is positional: run_model splits the packed buffer by these sizes."""
    shapes, sizes = get_policy_npy_shapes(SUPERCOMBO_SHAPES, is_supercombo=True)
    assert list(shapes) == ['desire', 'traffic_convention', 'action_t', 'prev_feat']
    assert shapes['desire'] == (8,)
    assert shapes['prev_feat'] == (1, 512)
    assert sizes == [8, 2, 2, 512]

  def test_split_layout_excludes_prev_feat(self):
    """Split models append the current feature inside the JIT."""
    shapes, sizes = get_policy_npy_shapes({**SPLIT_VISION_SHAPES, **SPLIT_POLICY_SHAPES})
    assert 'prev_feat' not in shapes
    assert list(shapes) == ['desire', 'traffic_convention', 'lateral_control_params', 'prev_desired_curv']
    assert sizes == [8, 2, 2, 25]

  def test_image_inputs_are_never_packed(self):
    shapes, _ = get_policy_npy_shapes({**SPLIT_VISION_SHAPES, **SPLIT_POLICY_SHAPES})
    assert not any('img' in k for k in shapes)

  def test_missing_desire_raises(self):
    with pytest.raises(ValueError, match="Desire key missing"):
      get_policy_npy_shapes({'img': (1, 12, 128, 256)})


class TestInferMHP:
  def test_single_hypothesis_plan(self):
    prod = ModelConstants.IDX_N * ModelConstants.PLAN_WIDTH
    assert _infer_mhp(2 * prod, prod) == (1, 0)

  def test_dtrv6_five_hypothesis_plan(self):
    """DTRV6 plan slice is 4955 = 5 * (2*495 + 1)."""
    prod = ModelConstants.IDX_N * ModelConstants.PLAN_WIDTH
    assert _infer_mhp(4955, prod) == (5, 1)

  def test_desired_curvature(self):
    assert _infer_mhp(2, ModelConstants.DESIRED_CURV_WIDTH) == (1, 0)


class TestParsePolicyOutputs:
  def _plan_shape(self, size):
    outs = {'plan': np.zeros((1, size), dtype=np.float32),
            'desire_state': np.zeros((1, ModelConstants.DESIRE_PRED_WIDTH), dtype=np.float32)}
    return Parser().parse_policy_outputs(outs)

  def test_single_hypothesis_plan_parses(self):
    out = self._plan_shape(2 * ModelConstants.IDX_N * ModelConstants.PLAN_WIDTH)
    assert out['plan'].shape == (1, ModelConstants.IDX_N, ModelConstants.PLAN_WIDTH)

  def test_multi_hypothesis_plan_parses_and_exposes_hypotheses(self):
    out = self._plan_shape(4955)
    assert out['plan'].shape == (1, ModelConstants.IDX_N, ModelConstants.PLAN_WIDTH)
    assert out['plan_hypotheses'].shape == (1, 5, ModelConstants.IDX_N, ModelConstants.PLAN_WIDTH)

  def test_infer_mhp_does_not_change_single_hypothesis_result(self):
    """Regression guard: the supercombo path must be bit-identical."""
    prod = ModelConstants.IDX_N * ModelConstants.PLAN_WIDTH
    raw = np.random.default_rng(0).standard_normal((1, 2 * prod)).astype(np.float32)
    shape = (ModelConstants.IDX_N, ModelConstants.PLAN_WIDTH)

    explicit = {'plan': raw.copy()}
    Parser().parse_mdn('plan', explicit, in_N=0, out_N=0, out_shape=shape)
    inferred = {'plan': raw.copy()}
    Parser().parse_mdn('plan', inferred, in_N=0, out_N=0, out_shape=shape, infer_mhp=True)

    assert np.array_equal(explicit['plan'], inferred['plan'])
    assert np.array_equal(explicit['plan_stds'], inferred['plan_stds'])


class TestFindDrivingPkl:
  """_find_driving_pkl decides which model actually runs, including the built-in fallback."""

  @staticmethod
  def _bundle(file_name):
    artifact = mock.Mock(fileName=file_name)
    return mock.Mock(models=[mock.Mock(artifact=artifact)])

  def test_env_override_wins(self, tmp_path, monkeypatch):
    from openpilot.selfdrive.modeld import modeld
    override = tmp_path / "override.pkl"
    override.write_bytes(b"x")
    monkeypatch.setenv('COMBINED_MODEL_PKL', str(override))
    path, bundle = modeld._find_driving_pkl(self._bundle("downloaded.pkl"))
    assert path == str(override)
    assert bundle is None, "env override must not apply bundle overrides"

  def test_uses_downloaded_bundle_pkl(self, tmp_path, monkeypatch):
    from openpilot.selfdrive.modeld import modeld
    monkeypatch.delenv('COMBINED_MODEL_PKL', raising=False)
    (tmp_path / "downloaded.pkl").write_bytes(b"x")
    monkeypatch.setattr('openpilot.common.hardware.hw.Paths.model_root', staticmethod(lambda: str(tmp_path)))
    bundle = self._bundle("downloaded.pkl")
    path, got = modeld._find_driving_pkl(bundle)
    assert path == os.path.join(str(tmp_path), "downloaded.pkl")
    assert got is bundle

  def test_falls_back_to_builtin_when_download_missing(self, tmp_path, monkeypatch):
    from openpilot.selfdrive.modeld import modeld
    monkeypatch.delenv('COMBINED_MODEL_PKL', raising=False)
    builtin = tmp_path / "driving_tinygrad.pkl"
    builtin.write_bytes(b"x")
    monkeypatch.setattr('openpilot.common.hardware.hw.Paths.model_root', staticmethod(lambda: str(tmp_path / "empty")))
    monkeypatch.setattr(modeld, 'modeld_pkl_path', lambda: str(builtin))
    path, bundle = modeld._find_driving_pkl(self._bundle("missing.pkl"))
    assert path == str(builtin)
    assert bundle is None

  def test_raises_when_nothing_available(self, tmp_path, monkeypatch):
    from openpilot.selfdrive.modeld import modeld
    monkeypatch.delenv('COMBINED_MODEL_PKL', raising=False)
    monkeypatch.setattr(modeld, 'modeld_pkl_path', lambda: str(tmp_path / "nope.pkl"))
    with pytest.raises(modeld.ModelUnavailable):
      modeld._find_driving_pkl(None)
