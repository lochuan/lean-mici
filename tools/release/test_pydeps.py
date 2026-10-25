"""Offline dependency files and target-process seam; no device/network."""
from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from openpilot.common import pydeps

PACKAGES = ['aiofiles', 'html5tagger', 'httptools', 'multidict', 'sanic', 'sanic-routing',
            'setuptools', 'tracerite', 'typing-extensions', 'ujson', 'uvloop', 'websockets']


def fixture(root, *, complete=False):
  archive = root / 'bundle.tar.gz'
  with tarfile.open(archive, 'w:gz') as stream:
    for name in PACKAGES if complete else ['sanic']:
      module = name.replace('-', '_')
      files = {f'{module}/__init__.py': b'# dependency fixture\n'}
      if complete:
        files[f'{module}-1.0.dist-info/METADATA'] = f'Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n'.encode()
      for path, data in files.items():
        info = tarfile.TarInfo('site-packages/' + path)
        info.size = len(data)
        stream.addfile(info, io.BytesIO(data))
  deps = {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'compressed_bytes': archive.stat().st_size,
          'packages': [{'name': name, 'version': '1.0', 'wheel': name.replace('-', '_') + '-1.0-py3-none-any.whl',
                        'sha256': 'b' * 64} for name in PACKAGES]}
  deps['id'] = hashlib.sha256(json.dumps([deps['archive_sha256'], deps['packages']], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
  archive.rename(root / (deps['id'] + '.tar.gz'))
  manifest = {'source_commit': 'a' * 40, 'uv_lock_sha256': 'c' * 64, 'pydeps': deps}
  (root / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
  return manifest


def target_process(*args, **kwargs):
  return subprocess.CompletedProcess(args, 0, stdout='{}', stderr='')


class DependencyTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory(dir='/tmp')
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.payload = self.root / 'payload'
    self.payload.mkdir()
    self.manifest = fixture(self.payload)
    self.deps = self.root / 'deps'

  def test_b1_install_requires_no_os_identity_or_system_images(self):
    # Neither VERSION/BUILD nor image hashes are available or required.
    site = pydeps.install(self.payload, self.deps, command=target_process)
    self.assertEqual(site.parent.name, self.manifest['pydeps']['id'])
    self.assertEqual((site.parent / 'COMPLETE').read_text(), site.parent.name)

  def launcher_environment(self, *, bundled=False):
    application = self.root / 'app'
    runtime = application / 'openpilot/common/pydeps.py'
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes((Path(__file__).resolve().parents[2] / 'openpilot/common/pydeps.py').read_bytes())
    (application / 'RELEASE_MANIFEST.json').write_text(json.dumps(self.manifest))
    if bundled:
      archive_name = self.manifest['pydeps']['id'] + '.tar.gz'
      (application / archive_name).write_bytes((self.payload / archive_name).read_bytes())
    launcher = (Path(__file__).resolve().parents[2] / 'launch_chffrplus.sh').read_text()
    function = launcher[launcher.index('function configure_pydeps'):launcher.index('function agnos_init')]
    function = function.replace('/usr/local/venv/bin/python', shlex.quote(sys.executable))
    for action in ('path', 'install'):
      function = function.replace(action + ' "$DIR"', action + ' "$DIR" --root ' + shlex.quote(str(self.deps)))
    result = subprocess.run(['bash', '-c', function + '\nconfigure_pydeps; env'],
                            env={'PATH': os.environ['PATH'], 'DIR': str(application), 'BLOCK': 'athena',
                                 'PYTHONPATH': '/foreign', 'PYTHONHOME': '/foreign'}, text=True, capture_output=True, check=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

  def test_i1_launcher_does_not_disable_bytecode_for_the_application(self):
    self.assertNotIn('PYTHONDONTWRITEBYTECODE', self.launcher_environment())

  def test_i2_startup_selects_complete_set_without_hashing_or_importing_packages(self):
    dependency = self.deps / 'sets' / self.manifest['pydeps']['id']
    (dependency / 'site-packages').mkdir(parents=True)
    (dependency / 'COMPLETE').write_text(dependency.name)
    environment = self.launcher_environment()
    self.assertEqual(environment['BLOCK'], 'athena')
    self.assertEqual(environment['PYTHONPATH'], str(self.root / 'app') + ':' + str(dependency.resolve() / 'site-packages'))
    self.assertNotIn('PYTHONHOME', environment)

  def test_launcher_installs_bundled_dependencies_on_clean_device_before_import(self):
    self.manifest = fixture(self.payload, complete=True)
    environment = self.launcher_environment(bundled=True)
    self.assertEqual(environment['BLOCK'], 'athena')
    site = self.deps / 'sets' / self.manifest['pydeps']['id'] / 'site-packages'
    result = subprocess.check_output([sys.executable, '-c', 'import sanic; print(sanic.__file__)'], env=environment, text=True)
    self.assertEqual(Path(result.strip()), site.resolve() / 'sanic/__init__.py')
    self.assertEqual((site.parent / 'COMPLETE').read_text(), self.manifest['pydeps']['id'])

  def test_i5_failed_probe_and_interrupted_rename_clean_staging(self):
    def missing_package(*args, **kwargs):
      raise subprocess.CalledProcessError(1, args, stderr='missing package')
    for fault in ('probe', 'rename'):
      with self.subTest(fault=fault):
        with patch('os.rename', side_effect=OSError('interrupted')) if fault == 'rename' else nullcontext():
          with self.assertRaises((OSError, subprocess.CalledProcessError)):
            pydeps.install(self.payload, self.deps, command=missing_package if fault == 'probe' else target_process)
        self.assertEqual(list(self.deps.glob('.staging-*')), [])
        self.assertFalse((self.deps / 'sets' / self.manifest['pydeps']['id']).exists())

  def test_install_reuses_complete_set_and_rejects_corrupt_transport(self):
    site = pydeps.install(self.payload, self.deps, command=target_process)
    with patch('os.statvfs', side_effect=AssertionError('no budget preflight')):
      self.assertEqual(pydeps.install(self.payload, self.deps, command=target_process), site)
    archive = self.payload / (self.manifest['pydeps']['id'] + '.tar.gz')
    archive.write_bytes(archive.read_bytes() + b'corrupt')
    with self.assertRaisesRegex(ValueError, 'hash/size'):
      pydeps.install(self.payload, self.deps, command=target_process)
    self.assertEqual(pydeps.dependency_path(self.payload, self.deps), site)

  def test_archive_escape_links_special_files_and_duplicates_are_rejected(self):
    for kind in ('escape', 'symlink', 'hardlink', 'device', 'duplicate'):
      with self.subTest(kind=kind):
        archive = self.payload / 'malicious.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
          member = tarfile.TarInfo('../escape' if kind == 'escape' else 'site-packages/bad')
          if kind in ('symlink', 'hardlink'):
            member.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
            member.linkname = '/tmp/escape'
          elif kind == 'device':
            member.type = tarfile.CHRTYPE
          stream.addfile(member)
          if kind == 'duplicate':
            stream.addfile(member)
        deps = self.manifest['pydeps']
        deps.update(archive_sha256=pydeps.file_hash(archive), compressed_bytes=archive.stat().st_size)
        deps['id'] = pydeps.dependency_id(deps)
        archive.rename(self.payload / (deps['id'] + '.tar.gz'))
        (self.payload / 'RELEASE_MANIFEST.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
          pydeps.install(self.payload, self.deps, command=target_process)
        self.assertFalse((self.root / 'escape').exists())
        self.assertEqual(list(self.deps.glob('.staging-*')), [])

  def test_missing_distribution_fails_real_probe_and_leaves_no_set(self):
    with self.assertRaises(subprocess.CalledProcessError):
      pydeps.install(self.payload, self.deps)
    self.assertEqual(list(self.deps.glob('.staging-*')), [])
    self.assertFalse((self.deps / 'sets' / self.manifest['pydeps']['id']).exists())

  def test_missing_complete_marker_blocks_only_lanlink_and_keeps_base_path(self):
    environment = self.launcher_environment()
    self.assertEqual(environment['BLOCK'], 'athena,lanlinkd')
    self.assertEqual(environment['PYTHONPATH'], str(self.root / 'app'))

  def test_rejects_oversized_unpacked_archive_before_extracting(self):
    self.replace_archive([tarfile.TarInfo('site-packages/oversized')], size=64 * 1024 * 1024 + 1)
    with self.assertRaisesRegex(ValueError, 'unpacked archive limit'):
      pydeps.install(self.payload, self.deps, command=target_process)
    self.assertEqual(list(self.deps.glob('.staging-*')), [])

  def replace_archive(self, members, size=0):
    archive = self.payload / (self.manifest['pydeps']['id'] + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as stream:
      for member in members:
        member.size = size
        stream.addfile(member)
    deps = self.manifest['pydeps']
    deps.update(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), compressed_bytes=archive.stat().st_size)
    deps['id'] = pydeps.dependency_id(deps)
    archive.rename(self.payload / (deps['id'] + '.tar.gz'))
    (self.payload / 'RELEASE_MANIFEST.json').write_text(json.dumps(self.manifest))


if __name__ == '__main__':
  unittest.main()
