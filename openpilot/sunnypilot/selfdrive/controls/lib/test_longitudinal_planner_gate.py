"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from unittest.mock import Mock, patch

from openpilot.cereal import custom, messaging
from opendbc.car import structs
from openpilot.common.test import OpenpilotTestCase
from openpilot.common.params import Params
from openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanSource as LocalPlanSource, LongitudinalPlannerSP
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner, LongitudinalPlanSource

V_EGO = 20.0
E2E_ACCEL = -3.0  # low enough that e2e wins the min() whenever it is a candidate


class MockSubMaster(dict):
  def __init__(self, services: dict):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.updated = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, 1)

  def all_checks(self, service_list=None) -> bool:
    return True


def build_sm(experimental_mode: bool = False) -> MockSubMaster:
  services = {}
  for service in ("radarState", "controlsState", "vehicleParameters", "carStateSP",
                  "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)

  car_state = messaging.new_message('carState')
  car_state.carState.vEgo = V_EGO
  car_state.carState.vCruise = 100.0
  car_state.carState.vCruiseCluster = 100.0
  services['carState'] = car_state.carState.as_reader()

  selfdrive_state = messaging.new_message('selfdriveState')
  selfdrive_state.selfdriveState.enabled = True
  selfdrive_state.selfdriveState.experimentalMode = experimental_mode
  services['selfdriveState'] = selfdrive_state.selfdriveState.as_reader()

  car_control = messaging.new_message('carControl')
  car_control.carControl.enabled = True
  services['carControl'] = car_control.carControl.as_reader()

  model = messaging.new_message('modelV2')
  model.modelV2.orientationRate.z = [0.01] * 33  # nonzero: a straight path divides by zero in SCC vision
  model.modelV2.velocity.x = [V_EGO] * 33
  model.modelV2.position.x = [float(i) for i in range(33)]
  model.modelV2.action.desiredAcceleration = E2E_ACCEL
  services['modelV2'] = model.modelV2.as_reader()

  return MockSubMaster(services)


def build_planner() -> LongitudinalPlanner:
  CP = structs.CarParams()
  CP.openpilotLongitudinalControl = True
  CP.steerRatio = 15.0
  CP.wheelbase = 2.7
  CP.longitudinalActuatorDelay = 0.2
  CP_SP = custom.CarParamsSP.new_message().as_reader()

  return LongitudinalPlanner(CP, CP_SP, init_v=V_EGO)


class TestPlannerGate(OpenpilotTestCase):
  def test_experimental_mode_controls_e2e_candidate(self):
    for experimental_mode in (False, True):
      planner = build_planner()
      with patch.object(Params, 'get_bool', return_value=not experimental_mode):
        planner.update(build_sm(experimental_mode))
      assert (planner.mpc.source == LongitudinalPlanSource.e2e) == experimental_mode

  def test_mpc_state_uses_measured_ego_for_stock_longitudinal(self):
    for openpilot_long in (False, True):
      planner = build_planner()
      planner.CP.openpilotLongitudinalControl = openpilot_long
      planner.v_desired_filter.x = 10.0
      planner.output_a_target = 0.2
      sm = build_sm()
      state = sm['carState'].as_builder()
      state.aEgo = 0.7
      sm['carState'] = state.as_reader()
      sm['controlsState'].longControlState = 'pid'
      with patch.object(planner.mpc, 'set_cur_state', wraps=planner.mpc.set_cur_state) as set_state, \
           patch.object(LongitudinalPlannerSP, 'update_targets', return_value=(30.0, 0.2)):
        planner.update(sm)
      speed, accel = set_state.call_args.args
      if openpilot_long:
        assert speed != V_EGO
        assert accel == 0.2
      else:
        assert speed == V_EGO
        assert abs(accel - 0.7) < 1e-6

  def test_local_targets_arbitrate_with_e2e(self):
    for source in (LocalPlanSource.sccVision, LocalPlanSource.sccMap, LocalPlanSource.speedLimitAssist):
      for experimental_mode in (False, True):
        planner = build_planner()
        planner.scc.update = Mock()
        planner.resolver.update = Mock()
        planner.resolver.distance = 0.0
        planner.sla.update = Mock()
        for target in (planner.scc.vision, planner.scc.map, planner.sla):
          target.output_v_target = 30.0
          target.output_a_target = 0.0
        target = {LocalPlanSource.sccVision: planner.scc.vision, LocalPlanSource.sccMap: planner.scc.map,
                  LocalPlanSource.speedLimitAssist: planner.sla}[source]
        target.output_v_target = 10.0
        target.output_a_target = -1.0
        planner.update(build_sm(experimental_mode))
        assert planner.source == source
        assert planner.output_v_target == 10.0
        assert (planner.mpc.source == LongitudinalPlanSource.e2e) == experimental_mode
