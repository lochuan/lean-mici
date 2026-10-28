import base64
import hashlib
import json
from pathlib import Path

import pytest

from opendbc import DBC_PATH, get_generated_dbcs
from opendbc.can.dbc_asset import decrypt_package
from tools.release.dbc_package import build_package
from tools.release import dbc_package
from tools.release.testdata.dbc_package import build_test_package
import subprocess


def test_package_round_trip_matches_complete_source_bytes(tmp_path):
  public = tmp_path / 'public'
  private = tmp_path / 'private/key.json'
  build_test_package(public, private, 'a' * 40, '2026-10-28')
  raw = (public / 'manifest.json').read_bytes()
  manifest = json.loads(raw)
  record = json.loads(private.read_bytes())
  assets = decrypt_package(raw, (public / 'package.enc').read_bytes(), base64.b64decode(record['key_b64']))
  assert assets == {
    'toyota_sienna_patched_lta_pt': get_generated_dbcs()['toyota_secoc_pt_generated'].encode(),
    'toyota_sienna_patched_lta_adas': (Path(DBC_PATH) / 'toyota_tss2_adas.dbc').read_bytes(),
  }
  assert {p.name for p in public.iterdir()} == {'manifest.json', 'package.enc'}
  assert len(record) == 9
  assert record['manifest_sha256'] == hashlib.sha256(raw).hexdigest()
  assert raw.endswith(b'\n') and b'key_b64' not in raw
  assert private.stat().st_mode & 0o777 == 0o600
  assert manifest['id'] == 'a' * 40 + '-' + record['key_id']


@pytest.mark.parametrize('fault', ['wrong_key', 'tamper', 'truncate', 'trailing'])
def test_rejects_invalid_encrypted_package(tmp_path, fault):
  public, private = tmp_path / 'public', tmp_path / 'key'
  build_test_package(public, private, 'b' * 40, '2026-10-28')
  raw = (public / 'manifest.json').read_bytes()
  encrypted = (public / 'package.enc').read_bytes()
  key = base64.b64decode(json.loads(private.read_bytes())['key_b64'])
  if fault == 'wrong_key':
    key = bytes(32)
  elif fault == 'tamper':
    encrypted = encrypted[:45] + bytes([encrypted[45] ^ 1]) + encrypted[46:]
  elif fault == 'truncate':
    encrypted = encrypted[:-1]
  else:
    encrypted += b'bad'
  # Also check framing/GCM when a compromised download supplies a matching public hash.
  manifest = json.loads(raw)
  manifest['package'].update(size=len(encrypted), sha256=hashlib.sha256(encrypted).hexdigest())
  with pytest.raises(ValueError):
    decrypt_package(json.dumps(manifest).encode(), encrypted, key)


def test_private_key_cannot_be_written_inside_public_output(tmp_path):
  with pytest.raises(ValueError):
    build_package(tmp_path / 'public', tmp_path / 'public/key.json', 'a' * 40, '2026-10-28')
  assert not (tmp_path / 'public').exists()


def test_repeated_builds_have_identical_plaintext_but_independent_keys(tmp_path):
  first = build_test_package(tmp_path / 'first', tmp_path / 'first.key', 'a' * 40, '2026-10-28')
  second = build_test_package(tmp_path / 'second', tmp_path / 'second.key', 'a' * 40, '2026-10-28')
  assert first['plaintext'] == second['plaintext'] and first['assets'] == second['assets']
  assert first['key_id'] != second['key_id']
  assert json.loads((tmp_path / 'first.key').read_bytes())['key_b64'] != json.loads((tmp_path / 'second.key').read_bytes())['key_b64']


@pytest.fixture
def clean_source(tmp_path, monkeypatch):
  root = tmp_path / 'source'
  root.mkdir()

  def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()

  def initialize(path):
    path.mkdir(exist_ok=True)
    git(path, 'init', '-q', '-b', 'lean-master')
    git(path, 'config', 'user.email', 'test@example.com')
    git(path, 'config', 'user.name', 'test')
    (path / 'source.txt').write_text('source')
    git(path, 'add', '.')
    git(path, 'commit', '-qm', 'source fixture')

  initialize(root)
  sub = root / 'opendbc_repo'
  initialize(sub)
  pinned = git(sub, 'rev-parse', 'HEAD')
  git(root, 'update-index', '--add', '--cacheinfo', f'160000,{pinned},opendbc_repo')
  git(root, 'commit', '-qm', 'pin opendbc')
  monkeypatch.setattr(dbc_package, 'SOURCE_ROOT', root, raising=False)
  return root, git(root, 'rev-parse', 'HEAD'), git


@pytest.mark.parametrize('fault', ['wrong_sha', 'dirty', 'untracked', 'submodule_dirty', 'gitlink_mismatch', 'not_lean_master_head'])
def test_builder_rejects_unverified_source_before_writing(clean_source, tmp_path, fault):
  root, revision, git = clean_source
  if fault == 'wrong_sha':
    revision = 'f' * 40
  elif fault == 'dirty':
    (root / 'source.txt').write_text('changed')
  elif fault == 'untracked':
    (root / 'new.py').write_text('untracked')
  elif fault == 'submodule_dirty':
    (root / 'opendbc_repo/source.txt').write_text('changed')
  elif fault == 'gitlink_mismatch':
    git(root / 'opendbc_repo', 'commit', '--allow-empty', '-qm', 'different dependency')
  else:
    git(root, 'checkout', '-qb', 'other')
    git(root, 'commit', '--allow-empty', '-qm', 'different source')
    revision = git(root, 'rev-parse', 'HEAD')
  with pytest.raises(ValueError):
    build_package(tmp_path / 'output', tmp_path / 'private/key', revision, '2026-10-28')
  assert not (tmp_path / 'output').exists()
  assert not (tmp_path / 'private/key').exists()


def test_clean_pinned_source_builds_with_its_actual_sha(clean_source, tmp_path):
  _, revision, _ = clean_source
  manifest = build_package(tmp_path / 'output', tmp_path / 'private/key', revision, '2026-10-28')
  assert manifest['source_sha'] == revision
