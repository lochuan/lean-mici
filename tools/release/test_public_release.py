"""OTA finalization installs the bundled set before the application can switch."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from tools.release.test_pydeps import fixture

ROOT = Path(__file__).resolve().parents[2]


class FinalizedDependencyTests(unittest.TestCase):
  def test_ota_installs_offline_before_ready_and_rejects_corrupt_bundle(self):
    for corrupt in (False, True):
      with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory(dir='/tmp') as temporary:
        root = Path(temporary)
        merged, finalized, dependencies = root / 'merged', root / 'finalized', root / 'deps'
        merged.mkdir()
        manifest = fixture(merged, complete=True)
        helper = merged / 'openpilot/common/pydeps.py'
        helper.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'openpilot/common/pydeps.py', helper)
        if corrupt:
          (merged / (manifest['pydeps']['id'] + '.tar.gz')).write_bytes(b'corrupt')
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'release']):
          subprocess.run(['git', '-C', str(merged), *args], check=True, capture_output=True)

        def run(command, cwd, dependency_root=dependencies):
          if 'install' in command:
            command = [*command, '--root', str(dependency_root)]
          return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.STDOUT)

        def consistent(ready, application=finalized):
          marker = application / '.overlay_consistent'
          if ready:
            marker.touch()
          else:
            marker.unlink(missing_ok=True)

        path = ROOT / 'openpilot/system/updated/updated.py'
        function = next(node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'finalize_update')
        namespace = {'os': os, 'shutil': shutil, 'Path': Path, 'sys': sys, 'run': run,
                     'cloudlog': SimpleNamespace(info=lambda message: None), 'set_consistent_flag': consistent,
                     'OVERLAY_MERGED': str(merged), 'FINALIZED': str(finalized)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        if corrupt:
          with self.assertRaises(subprocess.CalledProcessError):
            namespace['finalize_update']()
          self.assertFalse((finalized / '.overlay_consistent').exists())
        else:
          namespace['finalize_update']()
          dependency = dependencies / 'sets' / manifest['pydeps']['id']
          self.assertEqual((dependency / 'COMPLETE').read_text(), dependency.name)
          self.assertTrue((finalized / '.overlay_consistent').exists())
