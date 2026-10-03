from opendbc.car.car_helpers import interfaces
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.parameterized import parameterized
from openpilot.common.params import Params
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces
from openpilot.common.test import OpenpilotTestCase


FINGERPRINT_EXACT_MATCH = [TOYOTA.TOYOTA_RAV4_TSS2_2022]
FINGERPRINT_ANGLE_NO_MATCH = [TOYOTA.TOYOTA_RAV4_TSS2_2023]


class TestNNLCFingerprintBase(OpenpilotTestCase):

  @staticmethod
  def _setup_platform(car_name):
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, car_name)
    CI = CarInterface(CP, CP_SP)

    sunnypilot_interfaces.setup_interfaces(CI, Params())

    return CI

  @parameterized.expand(FINGERPRINT_EXACT_MATCH)
  def test_exact_fingerprint(self, car_name):
    CI = self._setup_platform(car_name)
    assert CI.CP_SP.neuralNetworkLateralControl.model.name != "MOCK" and not CI.CP_SP.neuralNetworkLateralControl.fuzzyFingerprint

  @parameterized.expand(FINGERPRINT_ANGLE_NO_MATCH)
  def test_no_fingerprint(self, car_name):
    CI = self._setup_platform(car_name)
    assert CI.CP_SP.neuralNetworkLateralControl.model.name == "MOCK"
