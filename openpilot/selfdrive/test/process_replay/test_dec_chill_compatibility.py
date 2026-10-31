import pytest

from openpilot.cereal import messaging
from openpilot.selfdrive.test.process_replay.migration import migrate_controlsState


@pytest.mark.parametrize('experimental_mode', [False, True])
def test_legacy_controls_state_preserves_experimental_mode(experimental_mode):
  old = messaging.new_message('controlsState')
  old.controlsState.deprecated.experimentalMode = experimental_mode
  _, migrated, _ = migrate_controlsState([(0, old.as_reader())])
  assert migrated[0].selfdriveState.experimentalMode == experimental_mode


def test_legacy_longitudinal_plan_sp_dec_slot_remains_readable():
  old = messaging.new_message('longitudinalPlanSP')
  old.longitudinalPlanSP.deprecated.dec.enabled = True
  old.longitudinalPlanSP.deprecated.dec.state = 'blended'
  with messaging.log.Event.from_bytes(old.to_bytes()) as decoded:
    assert decoded.longitudinalPlanSP.deprecated.dec.enabled
    assert decoded.longitudinalPlanSP.deprecated.dec.state == 'blended'
