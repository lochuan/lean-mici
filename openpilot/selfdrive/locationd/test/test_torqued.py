from unittest.mock import MagicMock, patch

from opendbc.car.structs import car
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd import torqued
from openpilot.selfdrive.locationd.torqued import TorqueEstimator


class TestTorqued(OpenpilotTestCase):
  def test_cal_percent(self):
    est = TorqueEstimator(car.CarParams())
    msg = est.get_msg()
    assert msg.lateralTorqueParameters.calPerc == 0

    for (low, high), min_pts in zip(est.filtered_points.buckets.keys(),
                                    est.filtered_points.buckets_min_points.values(), strict=True):
      for _ in range(int(min_pts)):
        est.filtered_points.add_point((low + high) / 2.0, 0.0)

    # enough bucket points, but not enough total points
    msg = est.get_msg()
    assert msg.lateralTorqueParameters.calPerc == (len(est.filtered_points) / est.min_points_total * 100 + 100) / 2

    # add enough points to bucket with most capacity
    key = list(est.filtered_points.buckets)[0]
    for _ in range(est.min_points_total - len(est.filtered_points)):
      est.filtered_points.add_point((key[0] + key[1]) / 2.0, 0.0)

    msg = est.get_msg()
    assert msg.lateralTorqueParameters.calPerc == 100

  def test_points_cache_written_outside_realtime(self):
    calls = []
    est = TorqueEstimator(car.CarParams())
    get_msg = est.get_msg
    est.get_msg = lambda **kw: calls.append(("get_msg", kw["with_points"])) or get_msg(**kw)
    params = MagicMock()
    params.put.side_effect = lambda key, _: calls.append(("put", key))
    with patch.object(torqued, "drop_realtime", lambda: calls.append("drop_realtime")), \
         patch.object(torqued, "config_realtime_process", lambda cores, priority: calls.append(("realtime", cores, priority))):
      torqued.write_points_cache(params, est, valid=True)
    assert calls == ["drop_realtime", ("get_msg", True), ("put", "LiveTorqueParameters"),
                     ("realtime", torqued.REALTIME_CORES, torqued.REALTIME_PRIORITY)]
