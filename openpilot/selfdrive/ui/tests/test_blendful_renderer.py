from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.sunnypilot.onroad import blendful as overlay


@pytest.mark.parametrize(('left_y', 'right_y', 'left_prob', 'right_prob', 'expected'), [
  (-2.0, 1.2, 0.9, 0.05, -0.2210526316),
  (-2.0, 1.2, 0.05, 0.9, -0.5789473684),
  (-2.0, 1.2, 0.9, 0.0, -0.2),
  (-2.0, 1.2, 0.0, 0.9, -0.6),
  (-2.0, 1.6, 0.95, 0.8, -0.2),
])
def test_lane_center_uses_model_right_positive_coordinates(monkeypatch, left_y, right_y, left_prob, right_prob, expected):
  renderer = overlay.BlendfulRenderer.__new__(overlay.BlendfulRenderer)
  renderer._readout = False
  renderer._alpha = FirstOrderFilter(1.0, 0.3, 0.05)
  renderer._trust = 0.9
  renderer._center_points = np.empty((0, 2))

  left = np.array([[10.0, left_y, 0.0], [20.0, left_y, 0.0]])
  right = np.array([[10.0, right_y, 0.0], [20.0, right_y, 0.0]])
  model_renderer = SimpleNamespace(
    _lane_lines=[None, SimpleNamespace(raw_points=left), SimpleNamespace(raw_points=right)],
    _map_line_to_polygon=lambda points, *args: points[:, :2],
  )
  telemetry = SimpleNamespace(blendfulTrust=0.9, blendfulLeftProb=left_prob, blendfulRightProb=right_prob,
                              blendfulLaneWidth=3.6)

  class Subscriber(dict):
    updated = {'modelDataV2SP': True}

  sm = Subscriber(modelDataV2SP=telemetry, modelV2=SimpleNamespace(laneLineStds=[0.1] * 4))
  monkeypatch.setattr(overlay, 'ui_state', SimpleNamespace(sm=sm))

  renderer.update(model_renderer, 2)

  np.testing.assert_allclose(renderer._center_points[:, 0], [10.0, 20.0])
  np.testing.assert_allclose(renderer._center_points[:, 1], expected, atol=5e-5)
