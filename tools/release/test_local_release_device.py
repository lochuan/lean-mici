"""Device switch behavior against real Git trees and local service/flag adapters."""
import importlib.util
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_local_release import make_repo, git
from test_pydeps import fixture, target_process

MODULE_PATH = Path(__file__).with_name('local_release.py')


class FileFlag:
  def __init__(self, path):
    self.path = path

  def read(self):
    return self.path.read_bytes() if self.path.exists() else None

  def write(self, value):
    if value is None:
      self.path.unlink(missing_ok=True)
    else:
      self.path.write_bytes(value)


class DeviceSwitchTests(unittest.TestCase):
  def setUp(self):
    self.assertTrue(MODULE_PATH.exists(), 'missing device-side detached switch module')
    spec = importlib.util.spec_from_file_location('local_release_device', MODULE_PATH)
    self.module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(self.module)
    self.temporary = tempfile.TemporaryDirectory(prefix='local-release-device-', dir='/tmp')
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.live, self.stage = self.root / 'live', self.root / 'stage'
    self.old = make_repo(self.live, 'lean-release')
    self.source = 'a' * 40
    self.payload = self.root / 'payload'
    self.payload.mkdir()
    self.manifest = fixture(self.payload)
    self.dependencies = self.root / 'deps'
    make_repo(self.stage, 'nav-local-release')
    (self.stage / 'tracked').write_text('new release\n')
    git(self.stage, 'add', '.')
    git(self.stage, 'commit', '-qm', f'flat release\n\nsource commit: nav-test@{self.source}')
    self.release = git(self.stage, 'rev-parse', 'HEAD')
    self.state = self.root / 'state.json'
    self.flag = FileFlag(self.root / 'DisableUpdates')
    self.events = []
    self.failure = None
    self.boot_id = 'boot-before-first-deployment'
    self.boot_started = '200'
    self.stack = patch.multiple(self.module, LIVE=self.live, STAGE=self.stage)
    self.stack.start()
    self.addCleanup(self.stack.stop)

  def command(self, *args):
    args = tuple(str(arg) for arg in args)
    if args == ('cat', '/proc/sys/kernel/random/boot_id'):
      return self.boot_id
    if args == ('systemctl', 'is-active', 'comma'):
      return 'inactive' if self.failure == 'service' else 'active'
    if args == ('awk', '$1 == "btime" { print $2 }', '/proc/stat'):
      return self.boot_started
    if args[:2] == ('sh', '-c'):
      if self.failure == 'manager':
        raise RuntimeError('manager missing or reset active')
      return ''
    if args[0] == 'git':
      if self.failure == 'fetch' and 'fetch' in args:
        raise RuntimeError('fetch failed')
      if self.failure == 'checkout' and 'checkout' in args and 'nav-local-release' in args and args[-1] == self.release:
        (self.live / 'tracked').write_text('partial mixed tree')
        raise RuntimeError('checkout failed halfway')
      return subprocess.check_output(args, text=True).strip()
    self.events.append(args)
    if args[1:3] == ('systemctl', 'stop'):
      self.assertEqual(self.flag.read(), b'1')
      if self.failure == 'stop':
        raise RuntimeError('stop failed')
    if args[1:] == ('reboot',):
      if self.failure == 'reboot':
        raise RuntimeError('reboot rejected')
      self.assertIn(git(self.live, 'rev-parse', 'HEAD'), (self.release, json.loads(self.state.read_text())['original_commit']))
    return ''

  def prepare(self):
    self.manifest['source_commit'] = self.source
    (self.payload / 'RELEASE_MANIFEST.json').write_text(json.dumps(self.manifest))
    self.module.prepare(self.source, self.flag, self.state, command=self.command, payload=self.payload,
                        dependency_root=self.dependencies, probe_command=target_process)

  def switch(self, action='consume', release=None):
    return self.module.switch(action, self.source, release or self.release, self.flag, self.state, command=self.command)

  def assert_recovered_without_reboot(self):
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.old)
    self.assertEqual((self.live / 'tracked').read_text(), 'committed source\n')
    self.assertIn(('sudo', 'systemctl', 'start', 'comma'), self.events)
    self.assertNotIn(('sudo', 'reboot'), self.events)

  def test_prepare_preserves_absent_zero_one_and_empty_exactly(self):
    for original in (None, b'0', b'1', b''):
      with self.subTest(original=original):
        self.state.unlink(missing_ok=True)
        self.flag.write(original)
        self.prepare()
        self.assertEqual(self.flag.read(), b'1')
        self.module.restore_updates(self.flag, json.loads(self.state.read_text()))
        self.assertEqual(self.flag.read(), original)

  def test_prepare_retry_does_not_overwrite_original_flag(self):
    self.flag.write(b'0')
    self.prepare()
    self.prepare()
    self.module.restore_updates(self.flag, json.loads(self.state.read_text()))
    self.assertEqual(self.flag.read(), b'0')

  def test_prepared_retry_rejects_changed_live_identity_without_changing_state_or_flag(self):
    self.prepare()
    recorded = self.state.read_bytes()
    git(self.live, 'checkout', '-q', '-b', 'outside-deployment')
    self.flag.write(b'0')
    with self.assertRaisesRegex(RuntimeError, 'prepared.*changed'):
      self.prepare()
    self.assertEqual(self.state.read_bytes(), recorded)
    self.assertEqual(self.flag.read(), b'0')

  def test_prepare_and_rollback_use_actual_live_branch_commit_and_original_one(self):
    git(self.live, 'checkout', '-q', '-b', 'previous-live')
    (self.live / 'tracked').write_text('actual previous release\n')
    git(self.live, 'commit', '-qam', 'previous deployed release')
    previous = git(self.live, 'rev-parse', 'HEAD')
    self.flag.write(b'1')
    self.prepare()
    state = json.loads(self.state.read_text())
    self.assertEqual(state['original_commit'], previous)
    self.assertEqual(state['original_ref'], 'refs/heads/previous-live')
    self.switch()
    self.switch('rollback')
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), previous)
    self.assertEqual(git(self.live, 'symbolic-ref', 'HEAD'), 'refs/heads/previous-live')
    self.assertEqual(self.flag.read(), b'1')

  def test_i4_installs_before_parameter_change_without_adding_transaction_identity(self):
    original_write = self.flag.write
    def check_write(value):
      if value == b'1':
        self.assertTrue((self.dependencies / 'sets' / self.manifest['pydeps']['id'] / 'COMPLETE').is_file())
      original_write(value)
    with patch.object(self.flag, 'write', side_effect=check_write):
      self.prepare()
    self.assertEqual(set(json.loads(self.state.read_text())), {'source_sha', 'source_branch', 'original_commit', 'original_ref',
                     'rollback_ref', 'boot_id', 'phase', 'disable_updates'})

  def test_missing_payload_refuses_prepare_before_flag_state_and_service_changes(self):
    with self.assertRaises(OSError):
      self.module.prepare(self.source, self.flag, self.state, command=self.command, payload=self.root / 'absent',
                          dependency_root=self.dependencies, probe_command=target_process)
    self.assertFalse(self.state.exists())
    self.assertIsNone(self.flag.read())
    self.assertEqual(self.events, [])

  def test_successful_consume_checks_sha_and_keeps_updates_disabled(self):
    self.prepare()
    self.switch()
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.release)
    self.assertEqual(git(self.live, 'rev-parse', 'refs/heads/lean-release'), self.old)
    self.assertEqual(self.flag.read(), b'1')
    self.assertEqual(self.events[-1], ('sudo', 'reboot'))

  def test_explicit_source_branch_is_recorded_and_checked_in_release_provenance(self):
    git(self.stage, 'commit', '--amend', '-qm', f'source commit: lean-master@{self.source}')
    self.release = git(self.stage, 'rev-parse', 'HEAD')
    self.module.prepare(self.source, self.flag, self.state, source_ref='refs/heads/lean-master', command=self.command, payload=self.payload,
                        dependency_root=self.dependencies, probe_command=target_process)
    self.assertEqual(json.loads(self.state.read_text())['source_branch'], 'lean-master')
    self.switch()
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.release)

  def test_second_deployment_after_reboot_rolls_back_to_first_release_and_keeps_one(self):
    self.prepare()
    self.switch()
    first_release = self.release
    self.boot_id = 'boot-after-first-deployment'
    self.source = 'b' * 40
    git(self.stage, 'commit', '--allow-empty', '-qm', f'source commit: nav-test@{self.source}')
    self.release = git(self.stage, 'rev-parse', 'HEAD')
    self.prepare()
    state = json.loads(self.state.read_text())
    self.assertEqual(state['original_commit'], first_release)
    self.assertEqual(state['original_ref'], 'refs/heads/nav-local-release')
    self.assertEqual(state['disable_updates'], '31')
    self.switch()
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.release)
    self.switch('rollback')
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), first_release)
    self.assertEqual(git(self.live, 'symbolic-ref', 'HEAD'), 'refs/heads/nav-local-release')
    self.assertEqual(self.flag.read(), b'1')

  def test_second_deployment_checkout_failure_recovers_first_release_pointer_and_one(self):
    self.prepare()
    self.switch()
    first_release = self.release
    self.boot_id = 'boot-after-first-deployment'
    self.source = 'b' * 40
    git(self.stage, 'commit', '--allow-empty', '-qm', f'source commit: nav-test@{self.source}')
    self.release = git(self.stage, 'rev-parse', 'HEAD')
    self.prepare()
    self.events.clear()
    self.failure = 'checkout'
    with self.assertRaisesRegex(RuntimeError, 'checkout failed'):
      self.switch()
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), first_release)
    self.assertEqual(git(self.live, 'symbolic-ref', 'HEAD'), 'refs/heads/nav-local-release')
    self.assertEqual(self.flag.read(), b'1')
    self.assertEqual(json.loads(self.state.read_text())['phase'], 'recovered')
    self.assertNotIn(('sudo', 'reboot'), self.events)
    self.assertEqual(self.events[-1], ('sudo', 'systemctl', 'start', 'comma'))

  def test_rebooting_state_requires_new_boot_matching_identity_flag_and_healthy_service(self):
    self.prepare()
    self.switch()
    deployed = json.loads(self.state.read_text())
    for fault in ('same boot', 'wrong release', 'wrong source', 'flag', 'service', 'manager', 'unknown phase', 'legacy without reboot'):
      with self.subTest(fault=fault):
        previous = dict(deployed)
        self.boot_id = deployed['boot_id'] if fault == 'same boot' else 'new-boot'
        self.flag.write(b'0' if fault == 'flag' else b'1')
        self.failure = fault
        if fault == 'wrong release':
          previous['release_sha'] = 'f' * 40
        if fault == 'wrong source':
          previous['source_sha'] = 'f' * 40
        if fault == 'unknown phase':
          previous['phase'] = 'unknown'
        if fault == 'legacy without reboot':
          previous.pop('boot_id')
        self.state.write_text(json.dumps(previous))
        recorded, flag = self.state.read_bytes(), self.flag.read()
        with self.assertRaises(RuntimeError):
          self.prepare()
        self.assertEqual(self.state.read_bytes(), recorded)
        self.assertEqual(self.flag.read(), flag)
        self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.release)

  def test_prepare_rejects_dirty_tree_and_detached_head_without_creating_state(self):
    (self.live / 'tracked').write_text('local work')
    with self.assertRaises(subprocess.CalledProcessError):
      self.prepare()
    self.assertFalse(self.state.exists())
    self.assertIsNone(self.flag.read())
    git(self.live, 'checkout', '-q', '--force', '--detach', self.old)
    with self.assertRaises(subprocess.CalledProcessError):
      self.prepare()
    self.assertFalse(self.state.exists())
    self.assertIsNone(self.flag.read())

  def test_verified_legacy_rebooting_state_becomes_a_fresh_transaction(self):
    import os
    self.prepare()
    self.switch()
    previous = json.loads(self.state.read_text())
    for key in ('boot_id', 'original_ref', 'rollback_ref', 'source_branch'):
      previous.pop(key)
    previous['disable_updates'] = '30'
    self.state.write_text(json.dumps(previous))
    os.utime(self.state, (100, 100))
    self.boot_id = 'new-boot'
    self.source = 'b' * 40
    self.prepare()
    current = json.loads(self.state.read_text())
    self.assertEqual(current['source_sha'], 'b' * 40)
    self.assertEqual(current['original_commit'], self.release)
    self.assertEqual(current['disable_updates'], '31')
    self.assertEqual(current['boot_id'], 'new-boot')

  def test_rolled_back_state_must_match_live_pointer_and_restored_flag_before_new_deployment(self):
    self.prepare()
    self.switch()
    self.switch('rollback')
    self.boot_id = 'boot-after-rollback'
    self.flag.write(b'1')
    recorded = self.state.read_bytes()
    with self.assertRaisesRegex(RuntimeError, 'rollback.*mismatch'):
      self.prepare()
    self.assertEqual(self.state.read_bytes(), recorded)

  def test_rebooting_state_is_persisted_before_requesting_reboot(self):
    self.prepare()
    def inspect_before_reboot(*args):
      if args == ('sudo', 'reboot'):
        state = json.loads(self.state.read_text())
        self.assertEqual(state['phase'], 'rebooting')
        self.assertEqual(state['release_sha'], self.release)
      return self.command(*args)
    self.module.switch('consume', self.source, self.release, self.flag, self.state, command=inspect_before_reboot)
    self.assertEqual(self.events[-1], ('sudo', 'reboot'))

  def test_wrong_source_message_never_stops_or_reboots(self):
    self.prepare()
    git(self.stage, 'commit', '--amend', '-qm', 'source commit: nav-test@' + 'f' * 40)
    wrong = git(self.stage, 'rev-parse', 'HEAD')
    with self.assertRaisesRegex(RuntimeError, 'source commit'):
      self.switch(release=wrong)
    self.assert_recovered_without_reboot()
    self.assertNotIn(('sudo', 'systemctl', 'stop', 'comma'), self.events)

  def test_consume_rejects_live_changes_after_prepare_without_recovering_over_them(self):
    self.prepare()
    git(self.live, 'commit', '--allow-empty', '-qm', 'unrelated change')
    unrelated = git(self.live, 'rev-parse', 'HEAD')
    recorded = self.state.read_bytes()
    self.flag.write(b'0')
    with self.assertRaisesRegex(RuntimeError, 'live.*changed'):
      self.switch()
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), unrelated)
    self.assertEqual(self.state.read_bytes(), recorded)
    self.assertEqual(self.flag.read(), b'0')
    self.assertEqual(self.events, [])

  def test_stale_consume_request_cannot_restore_another_transactions_parameters(self):
    self.flag.write(b'0')
    self.prepare()
    recorded = self.state.read_bytes()
    with self.assertRaisesRegex(RuntimeError, 'source SHA/state mismatch'):
      self.module.switch('consume', 'f' * 40, self.release, self.flag, self.state, command=self.command)
    self.assertEqual(self.flag.read(), b'1')
    self.assertEqual(self.state.read_bytes(), recorded)
    self.assertEqual(self.events, [])

  def test_rollback_with_stale_source_identity_is_rejected_without_mutation(self):
    self.prepare()
    self.switch()
    recorded = self.state.read_bytes()
    self.events.clear()
    with self.assertRaisesRegex(RuntimeError, 'rollback source mismatch'):
      self.module.switch('rollback', 'f' * 40, None, self.flag, self.state, command=self.command)
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.release)
    self.assertEqual(self.state.read_bytes(), recorded)
    self.assertEqual(self.flag.read(), b'1')
    self.assertEqual(self.events, [])

  def test_wrong_fetched_release_sha_never_reboots(self):
    self.prepare()
    with self.assertRaisesRegex(RuntimeError, 'release SHA'):
      self.switch(release='b' * 40)
    self.assert_recovered_without_reboot()

  def test_fetch_failure_starts_original_service_and_prints_rollback(self):
    self.prepare()
    self.failure = 'fetch'
    output = io.StringIO()
    with redirect_stderr(output), self.assertRaisesRegex(RuntimeError, 'fetch failed'):
      self.switch()
    self.assert_recovered_without_reboot()
    self.assertIsNone(self.flag.read())
    self.assertIn('setsid nohup', output.getvalue())
    self.assertIn('local-release.py rollback', output.getvalue())

  def test_rejected_reboot_restores_old_tree_and_starts_comma(self):
    self.prepare()
    self.failure = 'reboot'
    with self.assertRaisesRegex(RuntimeError, 'reboot rejected'):
      self.switch()
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.old)
    self.assertEqual(self.events[-1], ('sudo', 'systemctl', 'start', 'comma'))
    self.assertIsNone(self.flag.read())

  def test_failed_state_save_still_attempts_service_start(self):
    self.prepare()
    with patch.object(self.module, 'save', side_effect=OSError('disk full')), self.assertRaisesRegex(OSError, 'disk full'):
      self.switch()
    self.assert_recovered_without_reboot()

  def test_failed_parameter_restore_never_reboots_and_attempts_service_start(self):
    self.prepare()
    self.switch()
    self.events.clear()
    original_write = self.flag.write
    def cannot_restore(value):
      if value is None:
        raise RuntimeError('parameter restore failed')
      original_write(value)
    with patch.object(self.flag, 'write', side_effect=cannot_restore), self.assertRaisesRegex(RuntimeError, 'parameter restore failed'):
      self.switch('rollback')
    self.assert_recovered_without_reboot()
    self.assertEqual(self.flag.read(), b'1')

  def test_partial_checkout_is_recovered_before_service_start(self):
    self.prepare()
    self.failure = 'checkout'
    with self.assertRaisesRegex(RuntimeError, 'checkout failed'):
      self.switch()
    self.assert_recovered_without_reboot()
    self.assertIsNone(self.flag.read())

  def test_successful_checkout_command_with_wrong_head_or_dirty_tree_never_reboots(self):
    for fault in ('wrong HEAD', 'dirty tree'):
      with self.subTest(fault=fault):
        self.state.unlink(missing_ok=True)
        self.prepare()
        original_command = self.command
        def incorrect_checkout(*args, fault=fault, original_command=original_command):
          if 'checkout' in args and 'nav-local-release' in args:
            if fault == 'wrong HEAD':
              return ''
            result = original_command(*args)
            (self.live / 'tracked').write_text('corrupt checkout')
            return result
          return original_command(*args)
        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
          self.module.switch('consume', self.source, self.release, self.flag, self.state, command=incorrect_checkout)
        self.assert_recovered_without_reboot()

  def test_failed_stop_never_changes_tree_or_reboots(self):
    self.prepare()
    self.failure = 'stop'
    with self.assertRaisesRegex(RuntimeError, 'stop failed'):
      self.switch()
    self.assert_recovered_without_reboot()

  def test_rollback_restores_absence_and_reboots_only_after_verified_checkout(self):
    self.prepare()
    self.switch()
    self.events.clear()
    self.switch('rollback')
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.old)
    self.assertIsNone(self.flag.read())
    self.assertEqual(self.events[-1], ('sudo', 'reboot'))

  def test_cli_rollback_without_release_argument_preserves_deployed_sha_in_state(self):
    self.prepare()
    self.switch()
    self.module.switch('rollback', None, None, self.flag, self.state, command=self.command)
    state = json.loads(self.state.read_text())
    self.assertEqual(state['phase'], 'rolled-back')
    self.assertEqual(state['release_sha'], self.release)

  def test_rollback_restores_original_one(self):
    self.flag.write(b'1')
    self.prepare()
    self.switch()
    self.events.clear()
    self.switch('rollback')
    self.assertEqual(git(self.live, 'rev-parse', 'HEAD'), self.old)
    self.assertEqual(self.flag.read(), b'1')
    self.assertEqual(self.events[-1], ('sudo', 'reboot'))

  def test_failed_rollback_checkout_never_reboots_and_attempts_service_start(self):
    self.prepare()
    self.switch()
    self.events.clear()
    original_command = self.command
    def broken(*args):
      if 'checkout' in args:
        raise RuntimeError('disk failure')
      return original_command(*args)
    with self.assertRaisesRegex(RuntimeError, 'disk failure'):
      self.module.switch('rollback', self.source, self.release, self.flag, self.state, command=broken)
    self.assertNotIn(('sudo', 'reboot'), self.events)
    self.assertIn(('sudo', 'systemctl', 'start', 'comma'), self.events)
    self.assertEqual(self.flag.read(), b'1')


if __name__ == '__main__':
  unittest.main()
