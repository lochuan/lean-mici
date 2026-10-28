from types import SimpleNamespace

import pytest

from opendbc.can import dbc_asset
from openpilot.system.lanlinkd.tests.fake_params import FakeParams
from .test_dbc_activation import setup  # noqa: F401


@pytest.mark.parametrize('scenario', ['unprovisioned', 'revoked', 'revoked_during_init', 'source_changed', 'invalid_source', 'different_serial'])
def test_card_waits_without_can_output_when_selected_lta_has_no_resources(setup, monkeypatch, scenario):  # noqa: F811
  from openpilot.selfdrive.car import card
  resources, remote, _ = setup
  if scenario != 'unprovisioned':
    assert resources.activate('test-code')[0] == 200
  if scenario == 'revoked':
    remote.status_code, remote.status_body = 410, {'status': 'revoked'}
    assert resources.refresh()['revoked']
  revision = 'b' * 40 if scenario == 'source_changed' else None if scenario == 'invalid_source' else 'a' * 40
  serial = 'other-serial' if scenario == 'different_serial' else 'serial-123'

  class DeviceParams(FakeParams):
    def get(self, key, **kwargs):
      return self.data.get(key, 0 if kwargs.get('return_default') else None)

  params = DeviceParams({'CarPlatformBundle': {'platform': dbc_asset.PLATFORM}})
  sent = []
  panda_can = SimpleNamespace(can=[SimpleNamespace(address=0x7fe, dat=bytes(8), src=0)])
  monkeypatch.setattr(card, 'Params', lambda: params)
  monkeypatch.setattr(card.messaging, 'sub_sock', lambda *a, **k: object())
  monkeypatch.setattr(card.messaging, 'SubMaster', lambda *a, **k: object())
  monkeypatch.setattr(card.messaging, 'PubMaster', lambda *a, **k: SimpleNamespace(sock={'sendcan': SimpleNamespace(send=sent.append)}))
  monkeypatch.setattr(card.messaging, 'recv_one_retry', lambda *a: panda_can)
  def receive_can(*args, **kwargs):
    if scenario == 'revoked_during_init' and not resources.status()['revoked']:
      remote.status_code, remote.status_body = 410, {'status': 'revoked'}
      resources.refresh()
    return [panda_can]

  monkeypatch.setattr(card.messaging, 'drain_sock', receive_can)
  monkeypatch.setenv('SKIP_FW_QUERY', '1')
  monkeypatch.setattr(dbc_asset, 'default_store', lambda: dbc_asset.ProtectedDBCStore(resources.root, revision, lambda: serial))

  class WaitingForActivation(Exception):
    pass

  def wait(_seconds):
    raise WaitingForActivation

  monkeypatch.setattr(card.time, 'sleep', wait)
  with pytest.raises(WaitingForActivation):
    card.Car()
  assert not sent
  assert params.get('CarParams') is None
  assert params.get('CarPlatformBundle')['platform'] == dbc_asset.PLATFORM


def test_native_platform_selection_uses_the_same_activation_gate(setup, monkeypatch):  # noqa: F811
  import ast
  from pathlib import Path
  from openpilot.system.lanlinkd import vehicle_api

  resources, remote, offroad = setup
  monkeypatch.setattr(dbc_asset, 'STORAGE', resources.root)
  monkeypatch.setattr(dbc_asset, 'running_source_sha', lambda: 'a' * 40)
  monkeypatch.setattr(dbc_asset, 'hardware_serial', lambda: 'serial-123')
  params = FakeParams()
  dialogs = []
  # Compile the real selection method with a display adapter; no window/image is required.
  source = Path(__file__).resolve().parents[3] / 'selfdrive/ui/sunnypilot/layouts/settings/vehicle/platform_selector.py'
  cls = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.ClassDef) and node.name == 'PlatformSelector')
  method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '_set_platform')
  namespace = {'select_platform': vehicle_api.select_platform, 'ui_state': SimpleNamespace(params=params),
               'gui_app': SimpleNamespace(push_widget=dialogs.append), 'ConfirmDialog': lambda text, label: text, 'tr': lambda text: text}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
  selector = SimpleNamespace(_platforms={'LTA': {'platform': dbc_asset.PLATFORM}, 'Other': {'platform': 'TOYOTA_SIENNA_PATCHED'}},
                             refresh=lambda: None, _on_platform_change=None)
  namespace['_set_platform'](selector, 'LTA')
  assert params.get('CarPlatformBundle') is None and dialogs
  namespace['_set_platform'](selector, 'Other')
  assert params.get('CarPlatformBundle')['platform'] == 'TOYOTA_SIENNA_PATCHED'
  assert resources.activate('test-code')[0] == 200
  identifier = (resources.root / 'current').read_text()
  (resources.root / 'releases' / identifier / 'release.key').unlink()
  namespace['_set_platform'](selector, 'LTA')
  assert params.get('CarPlatformBundle')['platform'] == 'TOYOTA_SIENNA_PATCHED'
  assert resources.activate('test-code')[0] == 200
  namespace['_set_platform'](selector, 'LTA')
  assert params.get('CarPlatformBundle')['platform'] == dbc_asset.PLATFORM
  namespace['_set_platform'](selector, 'Other')
  offroad[0] = False
  remote.status_code, remote.status_body = 410, {'status': 'revoked'}
  assert resources.refresh()['cleanup_pending']
  before = list(params.puts)
  namespace['_set_platform'](selector, 'LTA')
  assert params.puts == before and '清理' in dialogs[-1]
