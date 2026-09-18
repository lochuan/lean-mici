"""CalibrationController 会话生命周期测试（不起真 SubMaster）。"""
import threading

import pytest

from openpilot.selfdrive.avoidanced.calibrate import CalibPair
from openpilot.system.lanlinkd.calibration import CalibrationController


def _pair(d: float = 20.0, y: float = -1.0, e: float = 0.3) -> CalibPair:
  """注入恒定前向残差 e 的配对（视觉 dRel = 雷达 dRel + e）。"""
  return CalibPair(d_radar=d, y_radar=y, d_vision=d + e, y_vision=y, v_ego=20.0)


class _StubController(CalibrationController):
  """跳过真实 SubMaster：直接填 _pairs 再走 _fit 语义。"""

  def stub_load(self, pairs) -> None:
    with self._lock:
      self._pairs = list(pairs)
      self._start_t = 0.0
      self._thread = None


def test_start_rejects_second_session():
  ctl = CalibrationController()
  alive = threading.Event()
  ctl._thread = threading.Thread(target=alive.wait, daemon=True)  # 保持存活
  ctl._thread.start()
  try:
    assert ctl.start() is False  # 已在跑 → 拒绝，不起第二个线程
  finally:
    alive.set()
    ctl._thread.join()


def test_fit_produces_result_with_constants_block():
  ctl = _StubController()
  pairs = [_pair(d=d, e=0.3) for d in (5, 10, 15, 20, 25, 30, 35, 40)]
  ctl.stub_load(pairs)
  ctl._fit(pairs)
  status = ctl.status()
  assert status["running"] is False
  res = status["last_result"]
  assert res is not None
  assert res["n_pairs"] == 8
  # 恒定前向残差 0.3m 应被 d_front 捕获
  assert res["d_front_m"] == pytest.approx(0.3, abs=0.05)
  assert "constants_block" in res
  assert "CAMERA_TO_FRONT" in res["constants_block"]
  assert res["insufficient"] is True  # 8 对 < 推荐下限 30，但要如实标记


def test_insufficient_pairs_sets_error_not_result():
  ctl = _StubController()
  pairs = [_pair(), _pair()]  # < MIN_FIT_PAIRS=3
  ctl.stub_load(pairs)
  ctl._fit(pairs)
  status = ctl.status()
  assert status["last_result"] is None
  assert status["last_error"] is not None
  assert "配对不足" in status["last_error"]


def test_recommended_min_pairs_flags_insufficient():
  ctl = _StubController()
  pairs = [_pair(d=d, e=0.1) for d in (10, 20, 30)]  # >= 3 但 < 30
  ctl.stub_load(pairs)
  ctl._fit(pairs)
  res = ctl.status()["last_result"]
  assert res is not None
  assert res["insufficient"] is True
