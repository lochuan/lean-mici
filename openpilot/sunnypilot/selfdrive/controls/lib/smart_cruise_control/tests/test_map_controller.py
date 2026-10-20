"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import math
import platform
from pathlib import Path


from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import R, SmartCruiseControlMap
from openpilot.common.test import OpenpilotTestCase

MAP_TARGET_EIGHT_POINTS_FIXTURE = Path(__file__).parents[6] / "selfdrive/bigmodeld/tests/fixtures/map_target_eight_points.json"

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState


class TestSmartCruiseControlMap(OpenpilotTestCase):

  def setup_method(self):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.reset_params()
    self.scc_m = SmartCruiseControlMap()

  def reset_params(self):
    self.params.put_bool("SmartCruiseControlMap", True, block=True)

    # TODO-SP: mock data from gpsLocation
    self.params.put("LastGPSPosition", "{}", block=True)
    self.params.put("MapTargetVelocities", "{}", block=True)

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
    waypoint_lon_deg = (40.0 / R) * (180.0 / math.pi)
    self.mem_params.put("LastGPSPosition", json.dumps({"latitude": 0.0, "longitude": 0.0}), block=True)
    self.mem_params.put("MapTargetVelocities",
                        json.dumps([{"latitude": 0.0, "longitude": waypoint_lon_deg, "velocity": 24.0}]), block=True)

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    self.assertAlmostEqual(self.scc_m.v_target, 24.0, delta=24.0 * 1e-6)

  def test_bigmodeld_map_target_writer_payload(self):
    # AU-8：读 C++ MapTargetWriter 的输出样例（test_bigmodeld.cc 的 test_map_target_eight_points_json
    # 断言 writer 输出与该文件逐字节相等）：velocity 是 m/s（target_speed_kph / 3.6），
    # 键只有 latitude/longitude/velocity。
    payload = MAP_TARGET_EIGHT_POINTS_FIXTURE.read_text()
    self.mem_params.put("LastGPSPosition", "{\"latitude\":37.4220000,\"longitude\":-122.0840000}", block=True)
    self.mem_params.put("MapTargetVelocities", payload, block=True)

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    # 最近点是第 0 个（0 m），目标速度最小 → 11.111 m/s
    self.assertAlmostEqual(self.scc_m.v_target, 11.111, delta=1e-3)
    # 状态机下一拍（v_cruise > v_target）才从 enabled 进 turning
    self.scc_m.update(True, False, 25.0, 0.0, 30.0)
    self.assertTrue(self.scc_m.is_active)

  def test_bigmodeld_empty_map_target_velocities(self):
    # 无效 CURVE / 断链时 bigmodeld 写 "[]"：SCC-map 没有候选，不会保持旧目标
    self.mem_params.put("LastGPSPosition", "{\"latitude\":37.4220000,\"longitude\":-122.0840000}", block=True)
    self.mem_params.put("MapTargetVelocities", "[]", block=True)

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    self.assertFalse(self.scc_m.is_active)
    self.assertEqual(self.scc_m.v_target, 100.0)  # min_v 初值：无候选

  def test_bigmodeld_missing_map_params(self):
    # 缺 key（首次上路 / mem Params 被清）：position 回 (0,0)、velocities 回 []，不抛异常
    self.mem_params.remove("LastGPSPosition")
    self.mem_params.remove("MapTargetVelocities")

    self.scc_m.update(True, False, 25.0, 0.0, 30.0)

    self.assertFalse(self.scc_m.is_active)
    self.assertEqual(self.scc_m.v_target, 100.0)

  # TODO-SP: mock data from modelV2 to test other states
