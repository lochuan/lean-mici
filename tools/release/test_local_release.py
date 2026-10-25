"""Local-source release tests: real temporary Git repos, no device/network/build."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('release_lib_local', HERE / 'release_lib.py')
release_lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_lib)
REAL_RUN = subprocess.run


def git(repo, *args):
  return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def make_repo(path, branch='main'):
  path.mkdir(parents=True)
  REAL_RUN(['git', 'init', '-q', '-b', branch, str(path)], check=True)
  git(path, 'config', 'user.email', 'test@example.com')
  git(path, 'config', 'user.name', 'test')
  (path / 'tracked').write_text('committed source\n')
  git(path, 'add', '.')
  git(path, 'commit', '-qm', 'fixture')
  return git(path, 'rev-parse', 'HEAD')


class LocalSourceTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory(prefix='local-release-test-', dir='/tmp')
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.source = self.root / 'source'
    self.build = self.root / 'build'
    self.pin = make_repo(self.root / 'submodule')
    make_repo(self.source, 'nav-test')
    cache = self.source / '.git/modules/tinygrad'
    cache.parent.mkdir()
    REAL_RUN(['git', 'clone', '-q', '--bare', str(self.root / 'submodule'), str(cache)], check=True)
    (self.source / '.gitmodules').write_text('[submodule "tinygrad"]\n path = tinygrad_repo\n url = https://invalid.test/tinygrad.git\n')
    git(self.source, 'add', '.gitmodules')
    git(self.source, 'update-index', '--add', '--cacheinfo', f'160000,{self.pin},tinygrad_repo')
    git(self.source, 'commit', '-qm', 'pin dependency')
    self.revision = git(self.source, 'rev-parse', 'HEAD')
    (self.source / 'tracked').write_text('uncommitted source must not be sent\n')
    (self.source / '.gitmodules').write_text('[submodule "wrong"]\n path = missing_repo\n url = https://invalid.test/wrong.git\n')
    self.environment = {**os.environ, 'GIT_ALLOW_PROTOCOL': 'file'}

  def receive_over_fake_ssh(self, command, **kwargs):
    if command[0] == 'ssh':
      self.assertNotIn('reboot', command[-1])
      self.assertNotIn('systemctl', command[-1])
      remote = command[-1].replace('/data/build', str(self.build)).replace('/usr/local/venv/bin/python', sys.executable)
      return REAL_RUN(['bash', '-c', remote], **kwargs)
    return REAL_RUN(command, **kwargs)

  def send(self, source_ref='refs/heads/nav-test'):
    self.assertTrue(callable(getattr(release_lib, 'send_local_source', None)), 'missing local source transfer')
    with patch.dict(os.environ, self.environment), patch.object(release_lib.subprocess, 'run', side_effect=self.receive_over_fake_ssh):
      return release_lib.send_local_source(self.source, 'comma@test', self.revision, source_ref)

  def test_explicit_local_branch_selects_approved_commit_not_nav_test_or_tag(self):
    git(self.source, 'branch', 'lean-master', self.revision)
    git(self.source, 'update-ref', 'refs/heads/nav-test', 'HEAD~1')
    git(self.source, 'tag', 'lean-master', self.revision + '^')
    self.assertEqual(self.send('refs/heads/lean-master'), self.revision)
    staged = self.build / '.git/local-source' / self.revision
    self.assertEqual(git(staged, 'rev-parse', 'refs/heads/source'), self.revision)

  def test_source_ref_rejects_tags_expressions_missing_branches_and_wrong_sha_before_write(self):
    git(self.source, 'tag', 'approved', self.revision)
    for source_ref in ('refs/tags/approved', self.revision, 'refs/heads/nav-test~0', 'refs/heads/missing', 'refs/heads/../nav-test'):
      with self.subTest(source_ref=source_ref), self.assertRaises((ValueError, subprocess.CalledProcessError)):
        self.send(source_ref)
      self.assertFalse(self.build.exists())
    git(self.source, 'branch', 'wrong', self.revision + '^')
    with self.assertRaisesRegex(ValueError, 'EXPECTED_SOURCE_SHA'):
      self.send('refs/heads/wrong')
    self.assertFalse(self.build.exists())

  def checkout_source(self, revision):
    script, _ = (HERE / 'device_release.sh').read_text().split('# 构建期间停 comma：', 1)
    script += '\ntrap - EXIT ERR\nBUILD="$1"\nLOCAL_SOURCE_SHA="$2"\nSRC_BRANCH=nav-test\ncheckout_source\n'
    return REAL_RUN(['bash', '-c', script, 'device-test', str(self.build), revision], env=self.environment, capture_output=True, text=True)

  def test_offline_bundle_transport_binds_source_lock_and_archive_before_device_write(self):
    from tools.release.test_pydeps import fixture

    (self.source / 'uv.lock').write_text('locked fixture')
    git(self.source, 'add', 'uv.lock')
    git(self.source, 'commit', '-qm', 'lock')
    self.revision = git(self.source, 'rev-parse', 'HEAD')
    payload = self.root / 'payload'
    payload.mkdir()
    manifest = fixture(payload)
    manifest.update(source_commit=self.revision, uv_lock_sha256=release_lib.pydeps.sha256(b'locked fixture'))
    (payload / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    with patch.object(release_lib.subprocess, 'run', side_effect=self.receive_over_fake_ssh):
      release_lib.send_local_pydeps(self.source, 'comma@test', self.revision, payload)
    received = self.build / '.git/pydeps' / self.revision
    self.assertEqual(json.loads((received / 'RELEASE_MANIFEST.json').read_text()), manifest)
    manifest['uv_lock_sha256'] = 'f' * 64
    (payload / 'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    with patch.object(release_lib.subprocess, 'run', side_effect=self.receive_over_fake_ssh) as external:
      with self.assertRaisesRegex(ValueError, 'lock'):
        release_lib.send_local_pydeps(self.source, 'comma@test', self.revision, payload)
      self.assertFalse(any(call.args[0][0] == 'ssh' for call in external.call_args_list))

  def test_uploads_exact_commits_from_uninitialized_submodule_cache(self):
    self.assertEqual(self.send(), self.revision)
    for sha in (self.revision, self.pin):
      staged = self.build / '.git/local-source' / sha
      self.assertEqual(git(staged, 'rev-parse', 'refs/heads/source'), sha)
      self.assertEqual(git(staged, 'rev-parse', '--is-shallow-repository'), 'true')
      self.assertNotIn('invalid.test', (staged / 'config').read_text())
      self.assertFalse((staged / 'hooks').exists())
    source = self.build / '.git/local-source' / self.revision
    self.assertEqual(git(source, 'show', 'refs/heads/source:tracked'), 'committed source')
    self.assertEqual((self.source / 'tracked').read_text(), 'uncommitted source must not be sent\n')

  def test_source_cache_retains_current_and_previous_generation_including_retries(self):
    revisions = []
    for generation in range(3):
      if generation:
        git(self.source, 'commit', '--allow-empty', '-qm', f'generation {generation}')
        self.revision = git(self.source, 'rev-parse', 'refs/heads/nav-test')
      revisions.append(self.send())
    self.send()
    cache = self.build / '.git/local-source'
    self.assertEqual({p.name for p in cache.iterdir() if p.is_dir()}, {self.pin, *revisions[-2:]})

  def test_cache_cleanup_works_without_python3_in_remote_path(self):
    binaries = self.root / 'remote-bin'
    binaries.mkdir()
    for executable in ('git', 'tar', 'mkdir'):
      (binaries / executable).symlink_to(shutil.which(executable))
    def restricted_receiver(command, **kwargs):
      if command[0] == 'ssh':
        remote = command[-1].replace('/data/build', str(self.build)).replace('/usr/local/venv/bin/python', sys.executable)
        return REAL_RUN(['/bin/bash', '-c', remote], env={**os.environ, 'PATH': str(binaries)}, **kwargs)
      return REAL_RUN(command, **kwargs)
    with patch.dict(os.environ, self.environment), patch.object(release_lib.subprocess, 'run', side_effect=restricted_receiver):
      self.assertEqual(release_lib.send_local_source(self.source, 'comma@test', self.revision), self.revision)
    self.assertTrue((self.build / '.git/local-source/generations.json').exists())

  def test_remote_stdout_is_never_forwarded_as_source_sha(self):
    def noisy_receiver(command, **kwargs):
      if command[0] == 'ssh':
        self.assertEqual(kwargs.get('stdout'), subprocess.DEVNULL)
      return self.receive_over_fake_ssh(command, **kwargs)
    with patch.dict(os.environ, self.environment), patch.object(release_lib.subprocess, 'run', side_effect=noisy_receiver):
      self.assertEqual(release_lib.send_local_source(self.source, 'comma@test', self.revision), self.revision)

  def test_source_must_match_approved_sha_before_device_writes(self):
    with patch.dict(os.environ, self.environment), patch.object(release_lib.subprocess, 'run', side_effect=self.receive_over_fake_ssh):
      with self.assertRaisesRegex(ValueError, 'EXPECTED_SOURCE_SHA'):
        release_lib.send_local_source(self.source, 'comma@test', 'f' * 40)
    self.assertFalse(self.build.exists())

  def test_source_uses_branch_when_head_and_same_named_tag_point_elsewhere(self):
    git(self.source, 'checkout', '-q', '--force', '--detach', 'HEAD~1')
    git(self.source, 'tag', 'nav-test', 'HEAD')
    self.assertNotEqual(git(self.source, 'rev-parse', 'HEAD'), self.revision)
    self.assertEqual(self.send(), self.revision)

  def test_fetched_root_must_match_requested_commit(self):
    self.send()
    root_cache = self.build / '.git/local-source' / self.revision
    wrong = git(root_cache, '-c', 'user.name=test', '-c', 'user.email=test@example.com',
                'commit-tree', f'{self.revision}^{{tree}}', '-m', 'wrong source with valid modules')
    git(root_cache, 'update-ref', 'refs/heads/source', wrong)
    result = self.checkout_source(self.revision)
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('commit 与指定 SHA 不符', result.stdout + result.stderr)

  def test_submodule_local_failure_never_invokes_network_fallback(self):
    self.send()
    shutil.rmtree(self.build / '.git/local-source' / self.pin)
    marker = self.root / 'network-attempt'
    script, _ = (HERE / 'device_release.sh').read_text().split('# 构建期间停 comma：', 1)
    script += '\ntrap - EXIT ERR\nBUILD="$1"\nLOCAL_SOURCE_SHA="$2"\nSRC_BRANCH=nav-test\n'
    script += 'git_fetch_retry() { touch "$NETWORK_MARKER"; git -C "$2" fetch -q "$SUB_SOURCE" "$SUB_PIN"; }\ncheckout_source\n'
    environment = {**self.environment, 'SUB_SOURCE': str(self.root / 'submodule'), 'SUB_PIN': self.pin}
    environment['NETWORK_MARKER'] = str(marker)
    result = REAL_RUN(['bash', '-c', script, 'device-test', str(self.build), self.revision], env=environment, capture_output=True, text=True)
    self.assertNotEqual(result.returncode, 0)
    self.assertFalse(marker.exists(), 'submodule fetch attempted a forbidden network fallback')

  def test_receiver_rejects_missing_commit_after_extract(self):
    import re
    def corrupt_receiver(command, **kwargs):
      if command[0] == 'ssh':
        remote = command[-1].replace('/data/build', str(self.build)).replace('/usr/local/venv/bin/python', sys.executable)
        remote = re.sub(r'(tar -xf - -C (\S+))', r'\1 && rm -rf \2/objects', remote, count=1)
        return REAL_RUN(['bash', '-c', remote], **kwargs)
      return REAL_RUN(command, **kwargs)
    with patch.dict(os.environ, self.environment), patch.object(release_lib.subprocess, 'run', side_effect=corrupt_receiver):
      with self.assertRaises(subprocess.CalledProcessError):
        release_lib.send_local_source(self.source, 'comma@test', self.revision)

  def test_missing_commit_fails_before_any_device_write(self):
    shutil.rmtree(self.source / '.git/modules/tinygrad')
    with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
      self.send()
    self.assertFalse(self.build.exists())

  def test_local_checkout_is_forced_clean_and_never_fetches_github(self):
    self.send()
    (self.build / 'untracked-output').write_text('stale')
    result = self.checkout_source(self.revision)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual(git(self.build, 'rev-parse', 'HEAD'), self.revision)
    self.assertEqual(git(self.build / 'tinygrad_repo', 'rev-parse', 'HEAD'), self.pin)
    self.assertFalse((self.build / 'untracked-output').exists())
    self.assertEqual((self.build / 'tracked').read_text(), 'committed source\n')
    (self.build / 'tracked').write_text('device modification')
    second = self.checkout_source(self.revision)
    self.assertEqual(second.returncode, 0, second.stderr)
    self.assertEqual((self.build / 'tracked').read_text(), 'committed source\n')

  def test_default_device_fetch_keeps_original_branch_flags_and_submodule_pin(self):
    self.send()
    trace = self.root / 'default-fetch.txt'
    script, _ = (HERE / 'device_release.sh').read_text().split('# 构建期间停 comma：', 1)
    # Keep real checkouts/clean and translate only the external fetch to fixture Git sources.
    script += r"""
trap - EXIT ERR
BUILD="$1"
LOCAL_SOURCE_SHA=""
git_fetch_retry() {
  printf '%s\n' "$*" >> "$TRACE"
  if [ "$1" = "-C" ]; then
    git -C "$2" fetch -q --update-shallow "$BUILD/.git/local-source/$PIN" refs/heads/source
  else
    git fetch -q --update-shallow "$BUILD/.git/local-source/$REVISION" refs/heads/source
  fi
}
checkout_source
"""
    environment = {**self.environment, 'TRACE': str(trace), 'PIN': self.pin, 'REVISION': self.revision,
                   'SRC_BRANCH': 'lean-master'}
    result = REAL_RUN(['bash', '-c', script, 'device-test', str(self.build)], env=environment, capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    calls = trace.read_text().replace(str(self.build), '$BUILD').splitlines()
    self.assertEqual(calls, ['fetch -4 -q --depth=1 origin lean-master',
                             f'-C $BUILD/tinygrad_repo fetch -4 -q --depth=1 origin {self.pin}'])
    self.assertEqual(git(self.build, 'rev-parse', 'HEAD'), self.revision)
    self.assertEqual(git(self.build / 'tinygrad_repo', 'rev-parse', 'HEAD'), self.pin)

  def test_missing_local_commit_refuses_network_fallback(self):
    self.send()
    missing = 'f' * 40
    result = self.checkout_source(missing)
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('本地', result.stdout + result.stderr)
    self.assertNotIn('transport', result.stderr)


COMMAND_STUB = '''#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as stream:
  stream.write(json.dumps([name, *args]) + '\\n')
if name == 'ssh':
  if os.environ.get('FAIL_PREPARE') and 'local-release.py prepare' in args[-1]:
    sys.exit(1)
  if os.environ.get('FAIL_POSTBOOT') and 'test "$(git rev-parse HEAD)"' in args[-1]:
    sys.exit(1)
  if 'kill -0' in args[-1]:
    sys.exit(1)
  if args[-1].endswith('local-release.py status'):
    print(os.environ.get('WORKER_PHASE', 'rebooting'))
  elif 'rev-parse' in args[-1]:
    print('b' * 40)
elif name == 'git':
  if args[:3] == ['push', '-f', 'fork'] and os.environ.get('FAIL_FORK'):
    sys.exit(1)
  if args[:1] == ['rev-parse']:
    print('b' * 40)
  elif args[:3] == ['remote', 'get-url', 'gitee']:
    print('git@gitee.test:release.git')
'''


class PublishScriptTests(unittest.TestCase):
  def run_publish(self, local=False, script_text=None, extra_env=None, pydeps_enabled=False):
    with tempfile.TemporaryDirectory(prefix='publish-release-test-', dir='/tmp') as temporary:
      root = Path(temporary)
      scripts = root / 'tools/release'
      scripts.mkdir(parents=True)
      (scripts / 'publish_release_from_device.sh').write_text(script_text or (HERE / 'publish_release_from_device.sh').read_text())
      (scripts / 'device_release.sh').touch()
      (scripts / 'local_release.py').touch()
      payload = root / 'artifact'
      payload.mkdir()
      if pydeps_enabled:
        runtime = root / 'openpilot/common/pydeps.py'
        runtime.parent.mkdir(parents=True)
        runtime.touch()
      # Only external source transport is replaced here; its real Git behavior is tested above.
      (scripts / 'release_lib.py').write_text('''import os, sys
import json
with open(os.environ['TRACE'], 'a') as stream:
  stream.write(json.dumps(['source-sender', *sys.argv[1:]]) + '\\n')
print(os.environ.get('SOURCE_OUTPUT', 'a' * 40))
if os.environ.get('FAIL_TRANSFER'): sys.exit(2)
''')
      bench = root / 'tools/bench'
      bench.mkdir()
      smoke = bench / 'smoke_after_build.sh'
      smoke.symlink_to(root / 'bin/stub')
      binaries = root / 'bin'
      binaries.mkdir()
      stub = binaries / 'stub'
      stub.write_text(COMMAND_STUB)
      stub.chmod(0o755)
      for command in ('ssh', 'scp', 'git', 'sleep', 'uv'):
        (binaries / command).symlink_to(stub)
      trace = root / 'trace.jsonl'
      fixture_environment_keys = (
        'SRC_BRANCH',
        'RELEASE_BRANCH',
        'ADB_SERIAL',
        'EXPECTED_SOURCE_SHA',
        'LOCAL_SOURCE_SHA',
        'LOCAL_SOURCE_REF',
        'PYDEPS_ARTIFACT',
        'FAIL_FORK',
        'FAIL_TRANSFER',
        'FAIL_PREPARE',
        'FAIL_POSTBOOT',
        'SOURCE_OUTPUT',
        'WORKER_PHASE',
      )
      environment = {key: value for key, value in os.environ.items() if key not in fixture_environment_keys}
      environment['PYDEPS_ARTIFACT'] = str(payload)
      environment.update(PATH=str(binaries) + ':' + os.environ['PATH'], DEVICE='comma@test', TRACE=str(trace))
      environment.update(extra_env or {})
      args = ['bash', str(scripts / 'publish_release_from_device.sh')]
      if local:
        environment.setdefault('EXPECTED_SOURCE_SHA', 'a' * 40)
        args.append('--local-source')
      result = REAL_RUN(args, env=environment, capture_output=True, text=True)
      calls = [json.loads(line.replace(str(root), '$ROOT')) for line in trace.read_text().splitlines()] if trace.exists() else []
      return result, calls

  def test_pydeps_public_release_rejected_before_any_external_write(self):
    result, calls = self.run_publish(pydeps_enabled=True)
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('公开 OTA', result.stderr)
    self.assertEqual(calls, [])

  def test_without_flag_keeps_exact_mainline_command_sequence(self):
    result, calls = self.run_publish()
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    expected = json.loads((HERE / 'testdata/mainline_publish_commands.json').read_text())
    self.assertEqual(calls, expected)

  def test_local_explicit_ref_reaches_sender_prepare_and_build(self):
    result, calls = self.run_publish(local=True, extra_env={'LOCAL_SOURCE_REF': 'refs/heads/lean-master'})
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    sender = next(call for call in calls if call[0] == 'source-sender')
    self.assertEqual(sender[-2:], ['--source-ref', 'refs/heads/lean-master'])
    prepare = next(call[-1] for call in calls if 'local-release.py prepare' in call[-1])
    self.assertIn('--source-ref refs/heads/lean-master', prepare)
    launch = next(call[-1] for call in calls if 'bash /tmp/device_release.sh' in call[-1])
    self.assertIn('SRC_BRANCH=lean-master RELEASE_BRANCH=nav-local-release', launch)

  def test_local_unsafe_ref_is_rejected_before_external_commands(self):
    result, calls = self.run_publish(local=True, extra_env={'LOCAL_SOURCE_REF': 'refs/heads/lean-master;touch /tmp/unwanted'})
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual(calls, [])

  def test_fixture_isolates_real_deployment_environment_without_changing_parent(self):
    source = '3667aca3a59e9a9e61da0cfe073251d93fffd466'
    parent_environment = {'EXPECTED_SOURCE_SHA': source, 'LOCAL_SOURCE_SHA': source,
                          'SRC_BRANCH': 'nav-test', 'RELEASE_BRANCH': 'nav-local-release', 'DEVICE': 'comma@10.0.0.27'}
    with patch.dict(os.environ, parent_environment):
      result, calls = self.run_publish(local=True)
      self.assertEqual(os.environ['EXPECTED_SOURCE_SHA'], source)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    launch = next(call[-1] for call in calls if call[0] == 'ssh' and 'bash /tmp/device_release.sh' in call[-1])
    self.assertIn('LOCAL_SOURCE_SHA=' + 'a' * 40, launch)
    self.assertTrue(all(call[-2] == 'comma@test' for call in calls if call[0] == 'ssh'))

  def test_fixture_ignores_parent_fault_injection_settings(self):
    parent_environment = {'FAIL_FORK': '1', 'FAIL_TRANSFER': '1', 'FAIL_PREPARE': '1', 'FAIL_POSTBOOT': '1',
                          'SOURCE_OUTPUT': 'unexpected parent stdout', 'WORKER_PHASE': 'failed'}
    with patch.dict(os.environ, parent_environment):
      for local in (False, True):
        with self.subTest(local=local):
          result, _ = self.run_publish(local=local)
          self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_default_fork_failure_retries_three_times_and_never_deploys(self):
    result, calls = self.run_publish(extra_env={'FAIL_FORK': '1'})
    self.assertNotEqual(result.returncode, 0)
    pushes = [call for call in calls if call[:4] == ['git', 'push', '-f', 'fork']]
    self.assertEqual(len(pushes), 3)
    self.assertFalse(any(call[0] == 'ssh' and 'reboot' in call[-1] for call in calls))

  def test_default_custom_release_branch_still_skips_gitee(self):
    result, calls = self.run_publish(extra_env={'SRC_BRANCH': 'big-uplink', 'RELEASE_BRANCH': 'big-release'})
    self.assertEqual(result.returncode, 0, result.stderr)
    launch = next(call[-1] for call in calls if call[0] == 'ssh' and 'nohup' in call[-1])
    self.assertIn('SRC_BRANCH=big-uplink RELEASE_BRANCH=big-release', launch)
    self.assertTrue(any(call[:4] == ['git', 'push', '-f', 'fork'] for call in calls))
    self.assertFalse(any(call[:4] == ['git', 'push', '-f', 'gitee'] for call in calls))

  def test_local_transfer_failure_never_starts_device_release(self):
    result, calls = self.run_publish(local=True, extra_env={'FAIL_TRANSFER': '1'})
    self.assertNotEqual(result.returncode, 0)
    self.assertFalse(any(call[0] == 'scp' or (call[0] == 'ssh' and 'nohup' in call[-1]) for call in calls))
    self.assertNotIn('回滚命令', result.stderr)
    self.assertIn('未调用 prepare', result.stderr)

  def test_prepare_failure_prints_rollback_because_original_flag_may_be_saved(self):
    result, calls = self.run_publish(local=True, extra_env={'FAIL_PREPARE': '1'})
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('local-release.py rollback', result.stderr)
    self.assertTrue(any('local-release.py prepare' in call[-1] for call in calls))
    self.assertFalse(any('bash /tmp/device_release.sh' in call[-1] for call in calls))

  def test_device_failure_and_postboot_sha_mismatch_print_detached_rollback(self):
    for environment in ({'WORKER_PHASE': 'recovered'}, {'FAIL_POSTBOOT': '1'}):
      with self.subTest(environment=environment):
        result, calls = self.run_publish(local=True, extra_env=environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('setsid nohup /usr/local/venv/bin/python /data/build/.git/local-release.py rollback', result.stderr)
        self.assertFalse(any(call[0] == 'ssh' and call[-1] == 'sudo reboot' for call in calls))

  def test_only_local_mode_runs_local_release_tests_and_prepares_before_build(self):
    _, local = self.run_publish(local=True)
    _, default = self.run_publish()
    self.assertEqual(sum('tools/release/test_local_release_device.py' in call for call in local), 1)
    self.assertFalse(any('tools/release/test_local_release_device.py' in call for call in default))
    prepare = next(i for i, call in enumerate(local) if 'local-release.py prepare' in call[-1])
    build = next(i for i, call in enumerate(local) if 'bash /tmp/device_release.sh' in call[-1])
    self.assertLess(prepare, build)

  def test_local_mode_requires_expected_source_before_external_commands(self):
    result, calls = self.run_publish(local=True, extra_env={'EXPECTED_SOURCE_SHA': ''})
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual(calls, [])

  def test_local_mode_rejects_polluted_source_stdout(self):
    result, calls = self.run_publish(local=True, extra_env={'SOURCE_OUTPUT': 'a' * 40 + '\necho injected'})
    self.assertNotEqual(result.returncode, 0)
    self.assertFalse(any(call[0] == 'scp' for call in calls))

  def test_local_flag_consumes_relstage_without_public_fetch_or_push(self):
    result, calls = self.run_publish(local=True)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertFalse(any(call[0] == 'git' and call[1] == 'push' for call in calls))
    launch = [call[-1] for call in calls if call[0] == 'ssh' and 'bash /tmp/device_release.sh' in call[-1]]
    self.assertEqual(len(launch), 1)
    self.assertIn('SRC_BRANCH=nav-test', launch[0])
    self.assertIn('RELEASE_BRANCH=nav-local-release', launch[0])
    self.assertIn('LOCAL_SOURCE_SHA=' + 'a' * 40, launch[0])
    consumption = [call[-1] for call in calls if call[0] == 'ssh' and 'local-release.py consume' in call[-1]]
    self.assertEqual(len(consumption), 1)
    self.assertIn('setsid nohup', consumption[0])
    self.assertIn('a' * 40 + ' ' + 'b' * 40, consumption[0])
    self.assertFalse(any(call[0] == 'ssh' and call[-1] == 'sudo reboot' for call in calls))
    self.assertTrue(any(call[0] == 'ssh' and 'git rev-parse HEAD' in call[-1] and 'b' * 40 in call[-1] for call in calls))
    self.assertFalse(any('/tmp/booted' in argument for call in calls for argument in call))


if __name__ == '__main__':
  unittest.main()
