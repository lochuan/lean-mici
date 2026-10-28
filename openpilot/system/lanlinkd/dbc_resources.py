"""Sienna license/provision lifecycle shared by LanLink and the always-running monitor."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import shutil
import threading

import requests

from opendbc.can import dbc_asset as asset

SERVICE_ORIGIN = 'https://tp.diaperastiko.top'
DOWNLOAD_ORIGIN = 'https://dl.diaperastiko.top'
STATUS_URL = SERVICE_ORIGIN + '/api/token-status'
ACTIVATION_LOCK = threading.Lock()


@dataclass
class Reply:
  status: int
  body: bytes
  url: str
  content_type: str
  redirected: bool = False


class ToyochipClient:
  def request(self, method, url, *, token='', payload=None, limit=16384):
    if not url.startswith((SERVICE_ORIGIN + '/', DOWNLOAD_ORIGIN + '/')):
      raise ValueError('untrusted resource origin')
    headers = {'Cache-Control': 'no-store'}
    if token:
      if not url.startswith(SERVICE_ORIGIN + '/'):
        raise ValueError('credentials cannot be sent to download origin')
      headers['Authorization'] = 'Bearer ' + token
    # TLS verification stays enabled; neither redirects nor exceptions may disclose credentials.
    with requests.request(method, url, json=payload, headers=headers, timeout=(5, 10), allow_redirects=False, stream=True) as response:
      raw = bytearray()
      for chunk in response.iter_content(65536):
        raw.extend(chunk)
        if len(raw) > limit:
          raise ValueError('resource exceeds limit')
      return Reply(response.status_code, bytes(raw), response.url, response.headers.get('Content-Type', ''), bool(response.history))


class DBCResources:
  def __init__(self, root: Path, revision: str | None, device_serial, is_offroad, client=None):
    self.root = root
    self.store = asset.ProtectedDBCStore(root, revision, device_serial)
    self.device_serial, self.is_offroad = device_serial, is_offroad
    self.client = client if client is not None else ToyochipClient()
    self._verified_signature = None

  @contextmanager
  def _locked(self):
    self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if self.root.is_symlink() or self.root.stat().st_mode & 0o077:
      raise ValueError('unsafe DBC directory')
    fd = os.open(self.root / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX)
      yield

  def _state(self):
    try:
      state = asset.parse_json(asset.private_read(self.root / 'state.json'))
      if not isinstance(state, dict):
        raise ValueError('invalid activation state')
      return state
    except FileNotFoundError:
      return {}

  def _save(self, state):
    self._verified_signature = None
    asset.private_write(self.root / 'state.json', asset.json_bytes(state))

  def _serial(self):
    try:
      serial = self.device_serial()
    except Exception:
      raise ValueError('hardware serial unavailable') from None
    if not isinstance(serial, str) or not serial.strip() or len(serial) > 256:
      raise ValueError('hardware serial unavailable')
    return serial

  def status(self):
    with self._locked():
      return self._status()

  def _status(self):
    state = self._state()
    ready = False
    issue = '' if self.store.revision is not None else 'source_unavailable'
    if state.get('revoked') or state.get('cleanup_pending') or not state.get('token'):
      self._verified_signature = None
    if state.get('token') and not state.get('revoked') and not state.get('cleanup_pending'):
      try:
        if state.get('device_id') != self._serial():
          issue = 'device_mismatch'
        elif self.store.revision is not None:
          signature = self.store.signature()
          if signature != self._verified_signature:
            self.store.load(next(iter(asset.SOURCES)))
            self._verified_signature = signature
          ready = True
      except ValueError:
        issue = 'serial_unavailable'
        self._verified_signature = None
      except (OSError, RuntimeError):
        self._verified_signature = None
    return {'activated': bool(state.get('token')) and not state.get('revoked'), 'ready': ready,
            'revoked': bool(state.get('revoked')), 'cleanup_pending': bool(state.get('cleanup_pending')),
            'can_activate': bool(self.is_offroad()) and not state.get('cleanup_pending', False),
            'source_sha': self.store.revision, 'issue': issue}

  def _download(self, url, limit, *, token=''):
    reply = self.client.request('GET', url, token=token, limit=limit)
    if reply.status != 200 or reply.url != url or reply.redirected or len(reply.body) > limit:
      raise ValueError('resource download failed')
    return reply.body

  def _provision(self, state):
    revision = self.store.revision
    if revision is None:
      return False
    raw = self._download(DOWNLOAD_ORIGIN + f'/dbc/sienna-patched-lta/{revision}/latest.json', 16384)
    latest = asset.parse_manifest(raw)
    identifier = latest['id']
    if latest['source_sha'] != revision:
      raise ValueError('DBC source mismatch')
    base = DOWNLOAD_ORIGIN + '/dbc/' + identifier
    manifest_raw = self._download(base + '/manifest.json', 16384)
    manifest = asset.parse_manifest(manifest_raw)
    if manifest_raw != raw or manifest['source_sha'] != revision or manifest['id'] != identifier:
      raise ValueError('DBC manifest mismatch')
    with self._locked():
      if self._state() != state or not self.is_offroad():
        return False
      asset.private_write(self.root / 'downloads' / 'status.json', asset.json_bytes({'id': identifier}))
    encrypted = self._download(base + '/package.enc', manifest['package']['size'])
    if len(encrypted) != manifest['package']['size'] or asset.sha256(encrypted) != manifest['package']['sha256']:
      raise ValueError('DBC download checksum mismatch')
    record = self._download(SERVICE_ORIGIN + '/keys/dbc/' + identifier, 4096, token=state['token'])
    with self._locked():
      if self._state() != state or not self.is_offroad():
        return False
      self.store.install(manifest_raw, encrypted, record)
      shutil.rmtree(self.root / 'downloads')
    return True

  def activate(self, code):
    if not ACTIVATION_LOCK.acquire(blocking=False):
      return 409, {'error': '激活正在进行，请稍后重试'}
    try:
      return self._activate(code)
    finally:
      ACTIVATION_LOCK.release()

  def _activate(self, code):
    with self._locked():
      if not self.is_offroad():
        return 409, {'error': '请停车后激活 Sienna Patched LTA'}
      state = self._state()
      if state.get('cleanup_pending'):
        self._cleanup(state)
      if not self.is_offroad() or self._state().get('cleanup_pending'):
        return 409, {'error': '请等待吊销清理完成'}
      if not isinstance(code, str) or not code.strip() or len(code) > 256:
        return 400, {'error': '请输入有效激活码'}
      try:
        serial = self._serial()
      except Exception:
        return 503, {'error': '无法读取车机硬件序列号，拒绝激活'}
      expected = self._state()
    try:
      url = SERVICE_ORIGIN + '/api/activate'
      reply = self.client.request('POST', url, payload={'code': code, 'device_id': serial, 'device_role': 'headunit'}, limit=4096)
      if reply.url != url or reply.redirected or reply.content_type.split(';')[0].strip().lower() != 'application/json':
        raise ValueError('invalid activation response')
      if reply.status != 200:
        status = reply.status if reply.status in (400, 401, 404, 409, 429, 503) else 502
        return status, {'error': '激活码已绑定其他车机' if status == 409 else '激活失败，请稍后重试'}
      body = asset.parse_json(reply.body, 4096)
      token = body.get('token') if isinstance(body, dict) else None
      if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
        raise ValueError('invalid activation token')
      with self._locked():
        if self._state() != expected:
          return 409, {'error': '授权状态已变化，请重新检查后激活'}
        state = {'token': token, 'device_id': serial, 'revoked': False, 'cleanup_pending': False}
        self._save(state)
        if not self.is_offroad() or self.store.revision is None:
          return 202, self._status()
      installed = self._provision(state)
      return (200 if installed else 202), self.status()
    except (OSError, ValueError, requests.RequestException):
      return 503, {'error': '激活或 DBC 下载失败，已保留现有资源，可停车后重试'}

  def refresh(self, *, check_token=True):
    with self._locked():
      state = self._state()
      if state.get('cleanup_pending'):
        self._cleanup(state)
        return self._status()
    if state.get('token') and not state.get('revoked') and check_token:
      try:
        reply = self.client.request('GET', STATUS_URL, token=state['token'], limit=4096)
        if (reply.status == 410 and reply.url == STATUS_URL and not reply.redirected and
            reply.content_type.split(';')[0].strip().lower() == 'application/json' and
            asset.parse_json(reply.body, 4096) == {'status': 'revoked'}):
          with self._locked():
            current = self._state()
            if current.get('token') == state['token']:
              current.update(revoked=True, cleanup_pending=True)
              self._save(current)
              self._cleanup(current)
      except (OSError, ValueError, requests.RequestException):
        pass
    # Resume provisioning without redeeming the code. Network runs outside the state lock.
    provision = None
    with self._locked():
      state = self._state()
      if (check_token and self.store.revision is not None and state.get('token') and not state.get('revoked') and
          not state.get('cleanup_pending') and self.is_offroad() and not self._status()['ready']):
        try:
          if state.get('device_id') == self._serial():
            provision = state
        except ValueError:
          pass
    if provision is not None:
      try:
        self._provision(provision)
      except (OSError, ValueError, requests.RequestException):
        pass
    return self.status()

  def _cleanup(self, state):
    if not self.is_offroad():
      return
    self._verified_signature = None
    # All managed artifacts live here. Shared Toyota DBCs and other vehicle settings are never removed.
    for path in self.root.iterdir():
      if path.name in ('state.json', 'lock'):
        continue
      if not self.is_offroad():
        return
      if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
      else:
        path.unlink(missing_ok=True)
    if self.is_offroad():
      self._save({'revoked': True, 'cleanup_pending': False})


def create_resources(params):
  return DBCResources(asset.STORAGE, asset.running_source_sha(), asset.hardware_serial, lambda: params.get_bool('IsOffroad'))
