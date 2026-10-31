from pathlib import Path
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.sunnypilot.system.params_migration import run_migration


def test_removed_dec_param_is_cleaned_once(tmp_path):
  params = Params(str(tmp_path))
  old_key = Path(params.get_param_path('DynamicExperimentalControl'))
  old_key.write_text('1')
  params.put_bool('ExperimentalMode', True, block=True)
  with patch('openpilot.sunnypilot.system.params_migration._migrate_car_platform_bundle'), \
       patch('openpilot.sunnypilot.system.params_migration._migrate_tesla_mads_screen_button'):
    run_migration(params)
    assert not old_key.exists()
    assert params.get_bool('ExperimentalMode')
    assert params.get_bool('DynamicExperimentalControlMigrated')
    run_migration(params)
    assert params.get_bool('ExperimentalMode')
