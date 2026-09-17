from openpilot.selfdrive.controls.controlsd import fuse_curvature


def test_fallback_when_avoidance_disabled():
  assert fuse_curvature(model=0.01, avoid=0.05, valid=True, enabled=False) == 0.01


def test_fuse_when_enabled():
  assert fuse_curvature(model=0.01, avoid=0.012, valid=True, enabled=True) == 0.012


def test_fallback_when_plan_invalid():
  assert fuse_curvature(model=0.01, avoid=0.05, valid=False, enabled=True) == 0.01
