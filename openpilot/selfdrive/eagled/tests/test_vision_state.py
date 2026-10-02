"""eagleState 的 HUD 事实:本道目标不过滤、视觉状态枚举与每条降级路径对应。"""

from openpilot.selfdrive.eagled import constants as C
from openpilot.selfdrive.eagled.tests.test_daemon_fusion import (ROI, _FakeCamera, _FakeDetector, _add_standard_lane, _box_at, _daemon)


def _state(pm):
  return [msg for service, msg in pm.sent if service == "eagleState"][-1].eagleState


def _vision_state(daemon, pm, ticks=2):
  for i in range(ticks):
    daemon.update(i * C.DT_5HZ)
  return str(_state(pm).visionState)


def test_own_lane_target_is_published_with_lane_zero():
  daemon, pm = _daemon(radar_points=[(30.0, 0.0), (30.0, 3.5)])
  _add_standard_lane(daemon)
  daemon.update(0.0)
  assert sorted(t.lane for t in _state(pm).targets) == [-1, 0]


def test_vision_state_ok_when_chain_healthy():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4), detector=_FakeDetector(detections=[_box_at(20.0, -1.0)]))
  assert _vision_state(daemon, pm) == "ok"


def test_vision_state_calibrating():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4), detector=_FakeDetector())
  daemon.sm["extrinsicsCalibration"].calStatus = "uncalibrated"
  assert _vision_state(daemon, pm) == "calibrating"


def test_vision_state_no_camera():
  class _DeadCamera:
    intrinsics = None

    def frame(self, horizon_row=None):
      return None

  daemon, pm = _daemon(camera=_DeadCamera())
  assert _vision_state(daemon, pm) == "noCamera"


def test_vision_state_no_model():
  class _BrokenDetector:
    def infer(self, roi, now=None):
      raise FileNotFoundError("pkl missing")

  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4), detector=_BrokenDetector())
  assert _vision_state(daemon, pm) == "noModel"


def test_vision_state_throttled():
  daemon, pm = _daemon(camera=_FakeCamera(frames=[ROI] * 4), detector=_FakeDetector())
  daemon.sm["deviceMotion"].inputsOK = False
  assert _vision_state(daemon, pm) == "throttled"


def test_vision_state_off_when_avoidance_disabled():
  daemon, pm = _daemon(enabled=False)
  assert _vision_state(daemon, pm) == "off"
