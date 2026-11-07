from pathlib import Path

import pytest

from openpilot.cereal import log
from openpilot.selfdrive.test.process_replay.migration import migrate_controlsState

# Serialized using the unmodified 6a6b3c99e schemas, before DEC was deprecated.
FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.mark.parametrize('experimental_mode', [False, True])
def test_legacy_controls_state_preserves_experimental_mode(experimental_mode):
  raw = (FIXTURES / f'legacy_controls_{str(experimental_mode).lower()}.bin').read_bytes()
  with log.Event.from_bytes(raw) as old:
    assert old.controlsState.deprecated.experimentalMode == experimental_mode
    _, migrated, _ = migrate_controlsState([(0, old)])
    assert migrated[0].selfdriveState.experimentalMode == experimental_mode


def test_legacy_longitudinal_plan_sp_dec_slot_remains_readable():
  raw = (FIXTURES / 'legacy_longitudinal_plan_sp.bin').read_bytes()
  with log.Event.from_bytes(raw) as decoded:
    assert decoded.longitudinalPlanSP.deprecated.dec.enabled
    assert decoded.longitudinalPlanSP.deprecated.dec.active
    assert decoded.longitudinalPlanSP.deprecated.dec.state == 'blended'
