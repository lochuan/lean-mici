"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import platform
from pathlib import Path

import pytest

from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap
from openpilot.common.test import OpenpilotTestCase

MAP_TARGET_EIGHT_POINTS_FIXTURE = Path(__file__).parents[6] / "selfdrive/bigmodeld/tests/fixtures/map_target_eight_points.json"

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState


class TestSmartCruiseControlMap(OpenpilotTestCase):

  def setup_method(self):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.reset_params()
    self.now_ns = 10_000_000_000
    self.scc_m = SmartCruiseControlMap(clock=lambda: self.now_ns)

  def reset_params(self):
    self.params.put_bool("SmartCruiseControlMap", True, block=True)

    self.mem_params.put("MapTargetVelocities", "[]", block=True)

  def test_initial_state(self):
    assert self.scc_m.state == VisionState.disabled
    assert not self.scc_m.is_active
    assert self.scc_m.output_v_target == V_CRUISE_UNSET
    assert self.scc_m.output_a_target == 0.

  def test_system_disabled(self):
    self.params.put_bool("SmartCruiseControlMap", False, block=True)
    self.scc_m.enabled = self.params.get_bool("SmartCruiseControlMap")

    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled
    assert not self.scc_m.is_active

  def test_disabled(self):
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(False, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled

  def test_transition_disabled_to_enabled(self):
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.enabled

  def test_moderate_curve(self):
    # Regression: `... / 2 * a` parsed as `(.../2)*a` instead of `.../(2*a)`,
    # making max_d ~11x too small so the moderate-curve branch never tripped.
    # v_ego=25, a_ego=0, tv=24: fixed max_d≈45m vs buggy ≈4m at a 40m waypoint.
    self.mem_params.put("MapTargetVelocities", json.dumps({
      "sample_time_ns": self.now_ns, "valid_until_ns": self.now_ns + 2_000_000_000,
      "points": [{"latitude": 37.422, "longitude": -122.084, "distance_m": 40, "velocity": 24.0}]}), block=True)

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    self.assertAlmostEqual(self.scc_m.v_target, 24.0, delta=24.0 * 1e-6)

  def test_bigmodeld_map_target_writer_payload(self):
    # AU-8：读 C++ MapTargetWriter 的输出样例（test_bigmodeld.cc 的 test_map_target_eight_points_json
    # 断言 writer 输出与该文件逐字节相等）：velocity 是 m/s（target_speed_kph / 3.6），
    # 新 envelope 的距离为沿路线距离，时间为 C4 BOOTTIME。
    payload = MAP_TARGET_EIGHT_POINTS_FIXTURE.read_text()
    self.mem_params.put("MapTargetVelocities", payload, block=True)

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    # 第 0 点是沿路线 0 m，目标速度最小 → 11.111 m/s
    self.assertAlmostEqual(self.scc_m.v_target, 11.111, delta=1e-3)
    # 状态机下一拍（v_cruise > v_target）才从 enabled 进 turning
    self.scc_m.update(True, False, 25.0, 0.0, 30.0)
    self.assertTrue(self.scc_m.is_active)

  def test_bigmodeld_empty_map_target_velocities(self):
    # 无效 CURVE / 断链时 bigmodeld 写 "[]"：SCC-map 没有候选，不会保持旧目标
    self.mem_params.put("MapTargetVelocities", "[]", block=True)

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    self.assertFalse(self.scc_m.is_active)
    self.assertEqual(self.scc_m.v_target, 0.0)  # 无目标立即清零

  def test_bigmodeld_missing_map_params(self):
    # 缺 key（首次上路 / mem Params 被清）：目标清零，不抛异常
    self.mem_params.remove("MapTargetVelocities")

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    self.assertFalse(self.scc_m.is_active)
    self.assertEqual(self.scc_m.v_target, 0.0)


def test_phone_distance_without_car_gps(distance_env):
  controller, params, _ = distance_env
  write_snapshot(params)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 24.


def test_distance_decreases_between_two_hz_snapshots(distance_env):
  controller, params, now = distance_env
  write_snapshot(params, points=[{"latitude": 37.422, "longitude": -122.084, "distance_m": 80, "velocity": 24}])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 100.
  now[0] += 500_000_000
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 24.


@pytest.fixture
def distance_env(tmp_path, monkeypatch):
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import map_controller as module
  params = Params(str(tmp_path / "params"))
  params.put_bool("SmartCruiseControlMap", True, block=True)
  monkeypatch.setattr(module, "Params", lambda *args: params)
  now = [10_000_000_000]
  controller = SmartCruiseControlMap(clock=lambda: now[0])
  return controller, params, now


def write_snapshot(params, sample_ns=10_000_000_000, valid_until_ns=12_000_000_000, points=None):
  params.put("MapTargetVelocities", json.dumps({"sample_time_ns": sample_ns, "valid_until_ns": valid_until_ns,
    "points": points if points is not None else [
      {"latitude": 37.422, "longitude": -122.084, "distance_m": 40, "velocity": 24}]}), block=True)


def test_transmission_delay_compensates_along_route_distance(distance_env):
  controller, params, now = distance_env
  write_snapshot(params, points=[{"latitude": 37.422, "longitude": -122.084, "distance_m": 80, "velocity": 24}])
  now[0] += 500_000_000
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 24.


def test_front_curve_is_not_truncated_by_geographic_nearest_point(distance_env):
  controller, params, _ = distance_env
  write_snapshot(params, points=[
    {"latitude": 38., "longitude": -122., "distance_m": 40, "velocity": 24},
    {"latitude": 37.422, "longitude": -122.084, "distance_m": 300, "velocity": 10}])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 24.


@pytest.mark.parametrize("v_ego,motion_valid", [(float("nan"), True), (float("inf"), True), (-1., True), (25., False)])
def test_invalid_motion_clears_held_target(distance_env, v_ego, motion_valid):
  controller, params, now = distance_env
  write_snapshot(params)
  for _ in range(2):
    controller.update(True, False, 25., 0., 30.)
  assert controller.is_active
  now[0] += 50_000_000
  controller.update(True, False, v_ego, 0., 30., motion_valid=motion_valid)
  assert controller.v_target == 0 and not controller.is_active
  assert controller.output_v_target == V_CRUISE_UNSET


def test_expiry_clears_target_without_stickiness(distance_env):
  controller, params, now = distance_env
  write_snapshot(params)
  controller.update(True, False, 25., 0., 30.)
  now[0] = 12_000_000_001
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0 and controller.target_lat == 0 and not controller.is_active


def test_duplicate_snapshot_cannot_extend_original_deadline(distance_env):
  controller, params, now = distance_env
  write_snapshot(params, valid_until_ns=10_500_000_000)
  controller.update(True, False, 25., 0., 30.)
  now[0] += 600_000_000
  write_snapshot(params, valid_until_ns=12_000_000_000)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


def test_out_of_order_snapshot_clears_target(distance_env):
  controller, params, now = distance_env
  write_snapshot(params)
  controller.update(True, False, 25., 0., 30.)
  now[0] += 50_000_000
  write_snapshot(params, sample_ns=9_900_000_000, valid_until_ns=11_900_000_000)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


def test_clock_backwards_clears_target(distance_env):
  controller, params, now = distance_env
  write_snapshot(params)
  now[0] += 100_000_000
  controller.update(True, False, 25., 0., 30.)
  now[0] -= 50_000_000
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


@pytest.mark.parametrize("payload", ["{", "null", "true", "[{}]", "{}", '"oops"',
  '{"sample_time_ns":0,"valid_until_ns":12000000000,"points":[]}',
  '{"sample_time_ns":true,"valid_until_ns":12000000000,"points":[]}',
  '{"sample_time_ns":10000000001,"valid_until_ns":12000000000,"points":[]}',
  '{"sample_time_ns":10000000000,"valid_until_ns":12000000001,"points":[]}'])
def test_malformed_envelope_clears_without_throwing(distance_env, payload):
  controller, params, _ = distance_env
  write_snapshot(params)
  controller.update(True, False, 25., 0., 30.)
  params.put("MapTargetVelocities", payload, block=True)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


@pytest.mark.parametrize("field,value", [("latitude", 91), ("longitude", -181), ("distance_m", -1),
  ("distance_m", 601), ("velocity", 0), ("velocity", -1), ("velocity", "24"),
  ("velocity", float("nan")), ("distance_m", True), ("latitude", None)])
def test_invalid_point_clears_without_throwing(distance_env, field, value):
  controller, params, _ = distance_env
  point = {"latitude": 37.422, "longitude": -122.084, "distance_m": 40, "velocity": 24}
  point[field] = value
  write_snapshot(params, points=[point])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


@pytest.mark.parametrize("distances", [[50, 40], list(range(9))])
def test_unsorted_or_too_many_points_rejected(distance_env, distances):
  controller, params, _ = distance_env
  write_snapshot(params, points=[{"latitude": 37.422, "longitude": -122.084, "distance_m": d, "velocity": 24}
                                 for d in distances])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


def test_same_target_hold_speed_reduction_deletion_and_override(distance_env):
  controller, params, now = distance_env
  point = {"latitude": 37.422, "longitude": -122.084, "distance_m": 40, "velocity": 24}
  write_snapshot(params, points=[point])
  for _ in range(2):
    controller.update(True, False, 25., 0., 30.)
  assert controller.is_active
  now[0] += 100_000_000
  point["distance_m"] = 300
  write_snapshot(params, sample_ns=now[0], points=[point])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 24  # 同坐标同速度仍在列表中，保持已有减速目标。
  controller.update(True, True, 25., 0., 30.)
  assert controller.state == MapState.overriding and not controller.is_active
  now[0] += 100_000_000
  point.update(distance_m=40, velocity=23)
  write_snapshot(params, sample_ns=now[0], points=[point])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 23 and controller.is_active
  params.put("MapTargetVelocities", "[]", block=True)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0 and not controller.is_active


@pytest.mark.parametrize("car_valid,gear,expected", [(False, "drive", 0), (True, "reverse", 0), (True, "drive", 24)])
def test_control_adapter_rejects_invalid_or_reverse_car_state(distance_env, monkeypatch, car_valid, gear, expected):
  from types import SimpleNamespace
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import map_controller as module
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.smart_cruise_control import SmartCruiseControl

  _, params, now = distance_env
  monkeypatch.setattr(module.time, "monotonic_ns", lambda: now[0])
  monkeypatch.setattr(module.time, "CLOCK_BOOTTIME", 7, raising=False)
  monkeypatch.setattr(module.time, "clock_gettime_ns", lambda clock_id: now[0])
  write_snapshot(params)

  class VehicleFeed(dict):
    def all_checks(self, service_list):
      assert service_list == ['carState']
      return car_valid

  feed = VehicleFeed(carState=SimpleNamespace(gearShifter=gear),
                     modelV2=SimpleNamespace(orientationRate=SimpleNamespace(z=[0.01]), velocity=SimpleNamespace(x=[25.])),
                     controlsState=SimpleNamespace(curvature=0.))
  control = SmartCruiseControl()
  control.update(feed, True, False, 25., 0., 30.)
  assert control.map.v_target == expected


def test_deeply_nested_json_does_not_escape_control_loop(distance_env):
  controller, params, _ = distance_env
  params.put("MapTargetVelocities", "[" * 10000 + "]" * 10000, block=True)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 0


def test_new_snapshot_resets_travelled_distance(distance_env):
  controller, params, now = distance_env
  write_snapshot(params)
  controller.update(True, False, 25., 0., 30.)
  now[0] += 500_000_000
  controller.update(True, False, 25., 0., 30.)
  write_snapshot(params, sample_ns=now[0], points=[
    {"latitude": 37.423, "longitude": -122.084, "distance_m": 75, "velocity": 24}])
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 100.


def test_stopped_interval_does_not_accumulate_forward_distance(distance_env):
  controller, params, now = distance_env
  write_snapshot(params, points=[{"latitude": 37.422, "longitude": -122.084, "distance_m": 90, "velocity": 24}])
  controller.update(True, False, 25., 0., 30.)
  now[0] += 500_000_000
  controller.update(True, False, 0., 0., 30.)
  now[0] += 500_000_000
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 100.  # 只前进 12.5 m，不能按最后车速将整个 1 s 都算成 25 m。


def test_linux_consumer_uses_boottime(distance_env, monkeypatch):
  from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import map_controller as module
  _, params, now = distance_env
  calls = []
  monkeypatch.setattr(module.platform, "system", lambda: "Linux")
  monkeypatch.setattr(module.time, "CLOCK_BOOTTIME", 7, raising=False)

  def boottime_ns(clock_id):
    calls.append(clock_id)
    return now[0]

  monkeypatch.setattr(module.time, "clock_gettime_ns", boottime_ns)
  controller = SmartCruiseControlMap()
  write_snapshot(params)
  controller.update(True, False, 25., 0., 30.)
  assert controller.v_target == 24. and calls == [7]
