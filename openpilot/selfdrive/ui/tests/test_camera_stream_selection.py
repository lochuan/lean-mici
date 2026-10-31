from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.ui.onroad import augmented_road_view as tizi
from openpilot.selfdrive.ui.mici.onroad import augmented_road_view as mici


@pytest.mark.parametrize('module', [tizi, mici])
@pytest.mark.parametrize('long_control,wide_available,speed,initial,expected', [
  (True, True, 0, 'narrow', 'wide'),
  (False, True, 0, 'wide', 'narrow'),
  (True, False, 0, 'wide', 'narrow'),
  (True, True, 100, 'wide', 'narrow'),
  (True, True, None, 'wide', 'wide'),
])
def test_wide_camera_follows_longitudinal_capability(module, long_control, wide_available, speed, initial, expected):
  streams = {'wide': module.WIDE_CAM, 'narrow': module.NARROW_ROAD_CAM}
  available = {module.WIDE_CAM, module.NARROW_ROAD_CAM} if wide_available else {module.NARROW_ROAD_CAM}
  view = SimpleNamespace(available_streams=available, stream_type=streams[initial], switch_stream=Mock())
  if speed is None:
    speed = (module.WIDE_CAM_MAX_SPEED + module.ROAD_CAM_MIN_SPEED) / 2
  sm = {'carParams': SimpleNamespace(openpilotLongitudinalControl=long_control), 'carState': SimpleNamespace(vEgo=speed)}
  module.AugmentedRoadView._switch_stream_if_needed(view, sm)
  if initial == expected:
    view.switch_stream.assert_not_called()
  else:
    view.switch_stream.assert_called_once_with(streams[expected])
