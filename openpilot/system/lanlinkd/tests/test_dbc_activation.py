import json
import pytest

from opendbc.can import dbc_asset
from openpilot.system.lanlinkd.dbc_resources import DBCResources, Reply, SERVICE_ORIGIN
from openpilot.system.lanlinkd import vehicle_api
from tools.release.testdata.dbc_package import build_test_package
from .fake_params import FakeParams


class FakeToyochip:
  """Network adapter: bind activation codes like the server and serve an isolated release."""
  def __init__(self, tmp_path, revision='a' * 40):
    self.public, self.private = tmp_path / 'public', tmp_path / 'private/key'
    self.manifest = build_test_package(self.public, self.private, revision, '2026-10-28')
    self.bound = None
    self.status_code, self.status_body = 200, {'status': 'active'}
    self.status_origin = SERVICE_ORIGIN
    self.status_type = 'application/json'
    self.status_redirected = False
    self.calls = []
    self.activation_number = 0

  def request(self, method, url, *, token='', payload=None, limit=0):
    self.calls.append((method, url, payload))
    if url.endswith('/api/activate'):
      assert payload['device_role'] == 'headunit'
      if self.bound and self.bound != payload['device_id']:
        return Reply(409, b'{"error":"bound_to_other_device"}', url, 'application/json')
      self.bound = payload['device_id']
      self.activation_number += 1
      return Reply(200, json.dumps({'token': f'{self.activation_number:043d}'}).encode(), url, 'application/json')
    if url.endswith('/api/token-status'):
      if self.status_code == 0:
        raise OSError('offline')
      return Reply(self.status_code, json.dumps(self.status_body).encode(),
                   self.status_origin + '/api/token-status', self.status_type, self.status_redirected)
    if url.endswith('/latest.json'):
      raw = (self.public / 'manifest.json').read_bytes()
    elif '/keys/dbc/' in url:
      raw = self.private.read_bytes()
    else:
      raw = (self.public / url.rsplit('/', 1)[1]).read_bytes()
    return Reply(200, raw, url, 'application/json')


@pytest.fixture
def setup(tmp_path):
  remote = FakeToyochip(tmp_path)
  offroad = [True]
  resources = DBCResources(tmp_path / 'device', 'a' * 40, lambda: 'serial-123', lambda: offroad[0], remote)
  return resources, remote, offroad


def test_activation_provisions_and_binds_to_hardware_serial(setup):
  resources, remote, _ = setup
  code, state = resources.activate('AAAA-BBBB-CCCC-DDDD')
  assert code == 200 and state['ready']
  assert remote.bound == 'serial-123'
  assert resources.store.load('toyota_sienna_patched_lta_pt').startswith(b'CM_')
  changed = DBCResources(resources.root, 'a' * 40, lambda: 'serial-456', lambda: True, remote)
  assert changed.activate('AAAA-BBBB-CCCC-DDDD')[0] == 409
  assert 'token' not in state and 'code' not in state


@pytest.mark.parametrize('name', dbc_asset.SOURCES)
def test_copied_complete_resources_cannot_load_on_another_device(setup, tmp_path, monkeypatch, name):
  import shutil
  from opendbc.can.dbc import DBC

  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  expected = resources.store.load(name)
  copied = tmp_path / 'copied-device'
  shutil.copytree(resources.root, copied)
  same_device = DBCResources(copied, 'a' * 40, lambda: 'serial-123', lambda: True, remote)
  assert same_device.store.load(name) == expected
  other_device = DBCResources(copied, 'a' * 40, lambda: 'serial-456', lambda: True, remote)
  monkeypatch.setattr(dbc_asset, 'default_store', lambda: other_device.store)
  # Exercise the actual loading seam, without the UI/status gate or the test plaintext adapter.
  with pytest.raises(PermissionError):
    DBC(name)


@pytest.mark.parametrize('serial', ['', None])
def test_protected_loading_refuses_unavailable_hardware_serial(setup, monkeypatch, serial):
  from opendbc.can.dbc import DBC

  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  unavailable = DBCResources(resources.root, 'a' * 40, lambda: serial, lambda: True, remote)
  monkeypatch.setattr(dbc_asset, 'default_store', lambda: unavailable.store)
  with pytest.raises(PermissionError):
    DBC('toyota_sienna_patched_lta_pt')


@pytest.mark.parametrize('serial, pc, allowed', [('serial-123', False, True), ('serial-456', False, False), ('serial-123', True, False)])
def test_default_store_provider_requires_matching_comma_hardware(setup, monkeypatch, serial, pc, allowed):
  from types import SimpleNamespace
  from openpilot.common import hardware

  resources, _, _ = setup
  assert resources.activate('test-code')[0] == 200
  monkeypatch.setattr(hardware, 'PC', pc)
  monkeypatch.setattr(hardware, 'HARDWARE', SimpleNamespace(get_serial=lambda: serial))
  store = dbc_asset.ProtectedDBCStore(resources.root, 'a' * 40)
  if allowed:
    assert store.load('toyota_sienna_patched_lta_pt').startswith(b'CM_')
  else:
    with pytest.raises(PermissionError):
      store.load('toyota_sienna_patched_lta_pt')


@pytest.mark.parametrize('status', [200, 401, 429, 503, 0])
def test_non_revocation_responses_preserve_usable_resources(setup, status):
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  remote.status_code, remote.status_body = status, {'status': 'revoked'}
  assert resources.refresh()['ready']
  assert (resources.root / 'current').exists()


def test_revocation_onroad_persists_pending_then_offroad_resumes_after_restart(setup, monkeypatch):
  resources, remote, offroad = setup
  assert resources.activate('test-code')[0] == 200
  monkeypatch.setattr(dbc_asset, 'default_store', lambda: resources.store)
  from opendbc.can.dbc import DBC
  assert DBC('toyota_sienna_patched_lta_pt').name == 'toyota_sienna_patched_lta_pt'
  offroad[0] = False
  remote.status_code, remote.status_body = 410, {'status': 'revoked'}
  state = resources.refresh()
  assert state['revoked'] and state['cleanup_pending'] and not state['ready']
  assert (resources.root / 'current').exists()
  with pytest.raises(PermissionError):
    DBC('toyota_sienna_patched_lta_pt')
  assert resources.activate('new-code')[0] == 409
  restarted = DBCResources(resources.root, 'a' * 40, lambda: 'serial-123', lambda: offroad[0], remote)
  remote.status_code = 0
  offroad[0] = True
  state = restarted.refresh()
  assert state['revoked'] and not state['cleanup_pending'] and not state['activated']
  assert {p.name for p in resources.root.iterdir()} == {'lock', 'state.json'}
  assert restarted.activate('new-code')[0] == 200
  assert restarted.status()['ready']


def test_410_from_other_origin_cannot_destroy_resources(setup):
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  remote.status_code, remote.status_body = 410, {'status': 'revoked'}
  remote.status_origin = 'https://untrusted.invalid'
  assert resources.refresh()['ready']


def test_only_sienna_lta_requires_activation_at_vehicle_selection(setup):
  resources, _, _ = setup
  cars = {'Sienna LTA': {'platform': 'TOYOTA_SIENNA_PATCHED_LTA'}, 'Other Toyota': {'platform': 'TOYOTA_SIENNA_PATCHED'}}
  params = FakeParams()
  assert vehicle_api.select_platform(params, 'Sienna LTA', cars, resources)[0] == 403
  assert vehicle_api.select_platform(params, 'Other Toyota', cars, resources)[0] == 204
  assert resources.activate('test-code')[0] == 200
  assert vehicle_api.select_platform(params, 'Sienna LTA', cars, resources)[0] == 204


@pytest.mark.parametrize('fault', ['html', 'redirect', 'wrong_body'])
def test_untrusted_410_never_triggers_cleanup(setup, fault):
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  remote.status_code, remote.status_body = 410, {'status': 'revoked'}
  if fault == 'html':
    remote.status_type = 'text/html'
  elif fault == 'redirect':
    remote.status_redirected = True
  else:
    remote.status_body = {'error': 'revoked'}
  assert resources.refresh()['ready']


def test_cleanup_failure_retains_pending_and_resumes_without_network(setup, monkeypatch):
  import shutil
  resources, remote, offroad = setup
  assert resources.activate('test-code')[0] == 200
  offroad[0] = False
  remote.status_code, remote.status_body = 410, {'status': 'revoked'}
  assert resources.refresh()['cleanup_pending']
  offroad[0] = True
  with monkeypatch.context() as fault:
    def disk_failure(*args, **kwargs):
      raise OSError('power loss')
    fault.setattr(shutil, 'rmtree', disk_failure)
    with pytest.raises(OSError):
      resources.refresh(check_token=False)
  restarted = DBCResources(resources.root, 'a' * 40, lambda: 'serial-123', lambda: True, remote)
  remote.status_code = 0
  assert restarted.status()['cleanup_pending']
  assert not restarted.refresh()['cleanup_pending']
  assert not restarted.status()['ready']


def test_readiness_is_invalidated_when_package_changes_or_source_changes(setup):
  resources, _, _ = setup
  assert resources.activate('test-code')[0] == 200
  assert resources.status()['ready']
  current = (resources.root / 'current').read_text()
  encrypted = resources.root / 'releases' / current / 'package.enc'
  encrypted.write_bytes(b'tampered')
  assert not resources.status()['ready']
  changed = DBCResources(resources.root, 'b' * 40, lambda: 'serial-123', lambda: True)
  assert not changed.status()['ready']


def test_hardware_serial_read_failure_prevents_activation(setup):
  resources, remote, _ = setup
  unavailable = DBCResources(resources.root, 'a' * 40, lambda: None, lambda: True, remote)
  assert unavailable.activate('test-code')[0] == 503
  assert not remote.calls


def test_monitor_runs_even_when_lanlink_is_disabled():
  from openpilot.system.manager.process_config import managed_processes
  params = FakeParams({'LanLinkEnabled': False})
  assert not managed_processes['lanlinkd'].should_run(False, params, None)
  monitor = managed_processes['dbc_monitor']
  assert monitor.enabled and monitor.module == 'openpilot.system.lanlinkd.dbc_monitor'
  assert monitor.should_run(False, params, None)
  assert monitor.should_run(True, params, None)


@pytest.mark.parametrize('fault', ['source', 'manifest_key_binding', 'package_digest'])
def test_provision_rejects_wrong_source_or_release_binding(setup, fault):
  resources, remote, _ = setup
  manifest_path = remote.public / 'manifest.json'
  manifest = json.loads(manifest_path.read_bytes())
  if fault == 'source':
    manifest['source_sha'] = 'b' * 40
    manifest['id'] = 'b' * 40 + '-' + manifest['key_id']
    manifest_path.write_bytes(dbc_asset.json_bytes(manifest))
  elif fault == 'manifest_key_binding':
    record = json.loads(remote.private.read_bytes())
    record['manifest_sha256'] = '0' * 64
    remote.private.write_bytes(dbc_asset.json_bytes(record))
  else:
    (remote.public / 'package.enc').write_bytes(b'invalid download')
  assert resources.activate('test-code')[0] == 503
  assert not resources.status()['ready']
  assert not (resources.root / 'current').exists()


def test_release_key_is_wrapped_and_never_persisted_in_plaintext(setup):
  import base64
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  record = json.loads(remote.private.read_bytes())
  key = base64.b64decode(record['key_b64'])
  for path in resources.root.rglob('*'):
    if path.is_file():
      assert path.stat().st_mode & 0o777 == 0o600
      raw = path.read_bytes()
      assert key not in raw and record['key_b64'].encode() not in raw


def test_offline_rollback_uses_installed_source_package_after_a_b_a(setup, tmp_path, monkeypatch):
  from opendbc.can.dbc import DBC

  first, _, _ = setup
  assert first.activate('test-code')[0] == 200
  second_remote = FakeToyochip(tmp_path / 'second-release', 'b' * 40)
  second = DBCResources(first.root, 'b' * 40, lambda: 'serial-123', lambda: True, second_remote)
  assert second.activate('test-code')[0] == 200

  class Offline:
    def request(self, *args, **kwargs):
      raise OSError('offline')

  rollback = DBCResources(first.root, 'a' * 40, lambda: 'serial-123', lambda: True, Offline())
  assert rollback.refresh()['ready']
  monkeypatch.setattr(dbc_asset, 'default_store', lambda: rollback.store)
  assert DBC('toyota_sienna_patched_lta_pt').name_to_msg['STEERING_LTA_2'].address == 0x131
  assert DBC('toyota_sienna_patched_lta_adas').msgs


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('fault', [FileNotFoundError, ValueError])
def test_monitor_checks_revocation_and_resumes_cleanup_without_valid_source(setup, monkeypatch, pending, fault):
  from openpilot.system.lanlinkd import dbc_monitor, dbc_resources

  resources, remote, offroad = setup
  assert resources.activate('test-code')[0] == 200
  remote.status_code, remote.status_body = 410, {'status': 'revoked'}
  if pending:
    offroad[0] = False
    assert resources.refresh()['cleanup_pending']
    offroad[0] = True

  def broken_source():
    raise fault('SOURCE_SHA unavailable')

  class StopMonitor(Exception):
    pass

  def stop(_seconds):
    raise StopMonitor

  monkeypatch.setattr(dbc_asset, 'source_sha', broken_source)
  monkeypatch.setattr(dbc_asset, 'STORAGE', resources.root)
  monkeypatch.setattr(dbc_asset, 'hardware_serial', lambda: 'serial-123')
  monkeypatch.setattr(dbc_resources, 'ToyochipClient', lambda: remote)
  from openpilot.common import params as params_module
  monkeypatch.setattr(params_module, 'Params', lambda: FakeParams({'IsOffroad': True}))
  monkeypatch.setattr(dbc_monitor.time, 'sleep', stop)
  with pytest.raises(StopMonitor):
    dbc_monitor.main()
  state = json.loads((resources.root / 'state.json').read_bytes())
  assert state == {'revoked': True, 'cleanup_pending': False}
  assert not (resources.root / 'releases').exists()


def test_serial_read_key_error_is_a_controlled_unavailable_status(setup):
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200

  def unreadable():
    raise KeyError('androidboot.serialno')

  unavailable = DBCResources(resources.root, 'a' * 40, unreadable, lambda: True, remote)
  assert unavailable.status()['issue'] == 'serial_unavailable'
  assert not unavailable.status()['ready']
  assert unavailable.activate('test-code')[0] == 503


def test_serial_mismatch_status_explicitly_reports_binding_problem(setup):
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  other = DBCResources(resources.root, 'a' * 40, lambda: 'serial-other', lambda: True, remote)
  status = other.status()
  assert status['issue'] == 'device_mismatch' and not status['ready']


def test_all_network_operations_release_the_local_state_lock(setup):
  import fcntl
  resources, remote, _ = setup
  checked = []

  class UnlockedNetwork:
    def request(self, method, url, **kwargs):
      with (resources.root / 'lock').open('rb') as lock:
        # Independent OS lock acquisition catches holding flock around any HTTP request.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_UN)
      checked.append(url)
      return remote.request(method, url, **kwargs)

  resources.client = UnlockedNetwork()
  assert resources.activate('test-code')[0] == 200
  assert resources.refresh()['ready']
  assert any('/api/token-status' in url for url in checked)
  assert any('/keys/dbc/' in url for url in checked)
  assert len(checked) == 6


def test_token_issued_during_onroad_transition_is_saved_and_provisioned_later(setup):
  resources, remote, offroad = setup

  class SwitchOnroad:
    def request(self, method, url, **kwargs):
      reply = remote.request(method, url, **kwargs)
      if url.endswith('/api/activate'):
        offroad[0] = False
      return reply

  resources.client = SwitchOnroad()
  code, status = resources.activate('test-code')
  assert code == 202 and status['activated'] and not status['ready']
  assert not (resources.root / 'releases').exists()
  offroad[0] = True
  assert resources.refresh()['ready']
  assert sum(url.endswith('/api/activate') for _, url, _ in remote.calls) == 1


def test_stale_410_cannot_remove_a_new_activation(setup):
  resources, remote, _ = setup
  assert resources.activate('test-code')[0] == 200
  renewed = DBCResources(resources.root, 'a' * 40, lambda: 'serial-123', lambda: True, remote)

  class RenewDuringStatus:
    def request(self, method, url, **kwargs):
      if url.endswith('/api/token-status'):
        reply = Reply(410, b'{"status":"revoked"}', url, 'application/json')
        assert renewed.activate('new-code')[0] == 200
        return reply
      return remote.request(method, url, **kwargs)

  resources.client = RenewDuringStatus()
  assert resources.refresh()['ready']
  assert renewed.status()['ready']


def test_download_cannot_install_after_concurrent_revocation(setup):
  resources, remote, _ = setup
  monitor = DBCResources(resources.root, 'a' * 40, lambda: 'serial-123', lambda: True, remote)

  class RevokeDuringKeyDownload:
    def request(self, method, url, **kwargs):
      reply = remote.request(method, url, **kwargs)
      if '/keys/dbc/' in url:
        remote.status_code, remote.status_body = 410, {'status': 'revoked'}
        assert monitor.refresh()['revoked']
      return reply

  resources.client = RevokeDuringKeyDownload()
  _, status = resources.activate('test-code')
  assert status['revoked'] and not status['ready']
  assert not (resources.root / 'releases').exists()


def test_parallel_activation_from_a_new_resource_context_is_rejected(setup):
  resources, remote, _ = setup
  other = DBCResources(resources.root, 'a' * 40, lambda: 'serial-123', lambda: True, remote)

  class ParallelActivation:
    def request(self, method, url, **kwargs):
      if url.endswith('/api/activate'):
        assert other.activate('second-code')[0] == 409
      return remote.request(method, url, **kwargs)

  resources.client = ParallelActivation()
  assert resources.activate('test-code')[0] == 200
  assert remote.activation_number == 1
