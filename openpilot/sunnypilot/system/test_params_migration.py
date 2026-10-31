from pathlib import Path
from unittest.mock import patch

from openpilot.common.params import Params
from openpilot.sunnypilot.system.params_migration import run_migration


def test_removed_dec_param_is_cleaned_after_backup_restore(tmp_path):
  params = Params(str(tmp_path))
  old_key = Path(params.get_param_path('DynamicExperimentalControl'))
  params.put_bool('ExperimentalMode', True, block=True)
  with patch('openpilot.sunnypilot.system.params_migration._migrate_car_platform_bundle'), \
       patch('openpilot.sunnypilot.system.params_migration._migrate_tesla_mads_screen_button'):
    for _ in range(2):
      old_key.write_text('1')
      run_migration(params)
      assert not old_key.exists()
      assert params.get_bool('ExperimentalMode')


def test_dec_cleanup_failure_does_not_stop_later_migrations(tmp_path):
  params = Params(str(tmp_path))
  with patch('pathlib.Path.unlink', side_effect=PermissionError('read only')), \
       patch('openpilot.sunnypilot.system.params_migration._migrate_car_platform_bundle') as later, \
       patch('openpilot.sunnypilot.system.params_migration._migrate_tesla_mads_screen_button'):
    run_migration(params)
    later.assert_called_once_with(params)
    assert params.get('OnroadScreenOffBrightnessMigrated') is not None
