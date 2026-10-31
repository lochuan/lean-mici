"""Boot and background AGNOS checks share launch_env's accepted versions."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class AgnosCompatibilityTests(unittest.TestCase):
  def test_i3_boot_accepts_both_current_and_transition_system(self):
    versions = subprocess.check_output(['bash', '-c', 'unset AGNOS_VERSION AGNOS_ACCEPTED_VERSIONS; source "$1"; echo "$AGNOS_ACCEPTED_VERSIONS"',
                                        'test', str(ROOT / 'launch_env.sh')], text=True).split()
    self.assertIn('19.6.29', versions)
    self.assertIn('20.1', versions)

  def background_actions(self, version, directory=ROOT):
    path = ROOT / 'openpilot/system/updated/updated.py'
    function = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'handle_agnos_update')
    actions = []
    hardware = types.SimpleNamespace(flash_agnos_update=lambda *args: actions.append('flash'), get_target_slot_number=lambda: 1)
    namespace = {'HARDWARE': types.SimpleNamespace(get_os_version=lambda: version), 'OVERLAY_MERGED': str(directory),
                 'run': lambda command, directory: subprocess.check_output(command, cwd=directory, text=True),
                 'set_consistent_flag': lambda value: actions.append(value),
                 'cloudlog': types.SimpleNamespace(info=lambda message: None), 'os': __import__('os'), 'Path': Path}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    with patch.dict(sys.modules, {'openpilot.common.hardware.comma.agnos': hardware}):
      namespace['handle_agnos_update']()
    return actions

  def test_i3_background_accepts_the_same_versions_and_keeps_other_update_behavior(self):
    self.assertEqual(self.background_actions('19.6.29'), [])
    self.assertEqual(self.background_actions('20.1'), [])
    self.assertEqual(self.background_actions('18.0'), [False, 'flash'])

  def test_matching_target_still_skips_update_without_an_accepted_list(self):
    with tempfile.TemporaryDirectory(dir='/tmp') as temporary:
      directory = Path(temporary)
      (directory / 'launch_env.sh').write_text('export AGNOS_VERSION=19.6.29\n')
      self.assertEqual(self.background_actions('19.6.29', directory), [])
