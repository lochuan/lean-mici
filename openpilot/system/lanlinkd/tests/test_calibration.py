"""CalibrationController 会话生命周期测试（不起真 SubMaster）。"""
import threading

import pytest

from openpilot.selfdrive.eagled.calibrate import CalibPair
from openpilot.system.lanlinkd.calibration import CalibrationController
from .fake_params import FakeParams


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
  ctl = CalibrationController(params=FakeParams())
  alive = threading.Event()
  ctl._thread = threading.Thread(target=alive.wait, daemon=True)  # 保持存活
  ctl._thread.start()
  try:
    assert ctl.start() is False  # 已在跑 → 拒绝，不起第二个线程
  finally:
    alive.set()
    ctl._thread.join()


def _pairs(n: int, e: float) -> list[CalibPair]:
  return [_pair(d=5.0 + i, e=e) for i in range(n)]


def _fitted(params, pairs) -> _StubController:
  ctl = _StubController(params=params)
  ctl.stub_load(pairs)
  ctl._fit(pairs)
  return ctl


def test_fit_proposes_current_value_plus_increment_without_saving():
  params = FakeParams({"CameraToFront": 1.6})
  ctl = _fitted(params, _pairs(8, 0.3))
  status = ctl.status()
  assert status["running"] is False
  res = status["last_result"]
  assert res is not None
  assert res["n_pairs"] == 8
  # 恒定前向残差 0.3m 应被 d_front 捕获；建议值 = 当前已存 1.6 + 0.3
  assert res["d_front_m"] == pytest.approx(0.3, abs=0.05)
  assert res["camera_to_front"]["current_m"] == 1.6
  assert res["camera_to_front"]["proposed_m"] == pytest.approx(1.9, abs=0.05)
  assert res["saved"] is False
  assert "constants_block" not in res  # 手贴代码块已删除（票 #7）
  assert res["insufficient"] is True  # 8 对 < 保存下限 30，但要如实标记
  assert params.puts == []  # 拟合不落盘，等用户点「保存并生效」


def test_apply_writes_proposed_value_and_marks_saved():
  params = FakeParams()
  ctl = _fitted(params, _pairs(40, 0.2))
  ok, payload = ctl.apply()
  assert ok is True
  assert params.puts == [("CameraToFront", pytest.approx(1.7))]
  assert payload["last_result"]["saved"] is True


def test_apply_twice_is_idempotent():
  # 写的是固定的绝对建议值：连点两次不重复累加增量
  params = FakeParams()
  ctl = _fitted(params, _pairs(40, 0.2))
  ctl.apply()
  ctl.apply()
  assert [v for _, v in params.puts] == [pytest.approx(1.7), pytest.approx(1.7)]


def test_apply_refuses_insufficient_pairs():
  params = FakeParams()
  ctl = _fitted(params, _pairs(10, 0.2))
  ok, message = ctl.apply()
  assert ok is False
  assert "10" in message
  assert params.puts == []


def test_apply_refuses_out_of_range_value():
  params = FakeParams({"CameraToFront": 2.4})
  ctl = _fitted(params, _pairs(40, 0.3))
  ok, message = ctl.apply()
  assert ok is False
  assert "2.5" in message
  assert params.puts == []


def test_apply_without_result_is_refused():
  ok, message = CalibrationController(params=FakeParams()).apply()
  assert ok is False
  assert message


def test_insufficient_pairs_sets_error_not_result():
  ctl = _StubController(params=FakeParams())
  pairs = [_pair(), _pair()]  # < MIN_FIT_PAIRS=3
  ctl.stub_load(pairs)
  ctl._fit(pairs)
  status = ctl.status()
  assert status["last_result"] is None
  assert status["last_error"] is not None
  assert "配对不足" in status["last_error"]


def test_recommended_min_pairs_flags_insufficient():
  ctl = _StubController(params=FakeParams())
  pairs = [_pair(d=d, e=0.1) for d in (10, 20, 30)]  # >= 3 但 < 30
  ctl.stub_load(pairs)
  ctl._fit(pairs)
  res = ctl.status()["last_result"]
  assert res is not None
  assert res["insufficient"] is True
