from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.ui.onroad import model_renderer as tizi
from openpilot.selfdrive.ui.mici.onroad import model_renderer as mici
from openpilot.selfdrive.ui.ui_state import UIStatus


@pytest.mark.parametrize('module', [tizi, mici])
@pytest.mark.parametrize('long_control', [False, True])
@pytest.mark.parametrize('color_count', [1, 2])
def test_path_does_not_require_allow_throttle(monkeypatch, module, long_control, color_count):
  draw = Mock()
  monkeypatch.setattr(module, 'draw_polygon', draw)
  monkeypatch.setattr(module.ui_state, 'rainbow_path', False)
  monkeypatch.setattr(module.ui_state, 'status', UIStatus.ENGAGED)
  gradient = SimpleNamespace(colors=[1] * color_count)
  renderer = SimpleNamespace(_path=SimpleNamespace(projected_points=np.ones((4, 2))),
                             _rect=SimpleNamespace(x=0, y=0), _lateral_active=True,
                             _longitudinal_control=long_control, _exp_gradient=gradient)
  module.ModelRenderer._draw_path(renderer)
  assert draw.call_count == 1
  used_gradient = draw.call_args.kwargs['gradient']
  assert (used_gradient is gradient) == (long_control and color_count > 1)
  if used_gradient is not gradient:
    assert used_gradient.colors == module.THROTTLE_COLORS


@pytest.mark.parametrize('module', [tizi, mici])
def test_gradient_update_skips_stock_longitudinal(module):
  renderer = SimpleNamespace(_longitudinal_control=False)
  module.ModelRenderer._update_experimental_gradient(renderer)


def test_mici_disengaged_path(monkeypatch):
  draw = Mock()
  monkeypatch.setattr(mici, 'draw_polygon', draw)
  monkeypatch.setattr(mici.ui_state, 'rainbow_path', False)
  monkeypatch.setattr(mici.ui_state, 'status', UIStatus.DISENGAGED)
  renderer = SimpleNamespace(_path=SimpleNamespace(projected_points=np.ones((4, 2))),
                             _rect=SimpleNamespace(x=0, y=0), _longitudinal_control=True,
                             _exp_gradient=SimpleNamespace(colors=[1, 2]))
  mici.ModelRenderer._draw_path(renderer)
  assert draw.call_count == 1
  assert draw.call_args.args[2].a == 90
