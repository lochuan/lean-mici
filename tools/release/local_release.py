#!/usr/local/venv/bin/python
"""Device-side local release consume/rollback. Launch mutations via setsid nohup."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

LIVE = Path('/data/openpilot')
STAGE = Path('/data/relstage')
STATE = Path('/data/build/.git/local-release-state.json')
BASELINE = 'e72bce67aff8b593f2946d367f14eb6195e03de3'
WORKER = '/data/build/.git/local-release.py'
ROLLBACK = f'(setsid nohup /usr/local/venv/bin/python {WORKER} rollback > /tmp/rollback.log 2>&1 < /dev/null &)'


def run(*args):
  return subprocess.check_output([str(arg) for arg in args], text=True).strip()


def require(condition, message):
  if not condition:
    raise RuntimeError(message)


class UpdateFlag:
  """Only DisableUpdates is persisted/restored; no other params are copied."""
  def read(self):
    path = Path('/data/params/d/DisableUpdates')
    return path.read_bytes() if path.exists() else None

  def write(self, value):
    sys.path.insert(0, str(LIVE))
    from openpilot.common.params import Params, params_put
    params = Params()
    if value is None:
      if self.read() is not None:
        params.remove('DisableUpdates')
    else:
      key = params.check_key('DisableUpdates')
      require(params_put(params.p, key, value, len(value), True) == 0, 'DisableUpdates write failed')
    require(self.read() == value, 'DisableUpdates verification failed')


def save(state_path, state):
  temporary = state_path.with_suffix('.tmp')
  with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
    json.dump(state, stream)
    stream.flush()
    os.fsync(stream.fileno())
  temporary.replace(state_path)
  descriptor = os.open(state_path.parent, os.O_RDONLY)
  try:
    os.fsync(descriptor)
  finally:
    os.close(descriptor)


def restore_updates(updates, state):
  original = state['disable_updates']
  updates.write(bytes.fromhex(original) if original is not None else None)


def prepare(source_sha, updates, state_path=STATE, *, command=run):
  require(re.fullmatch('[0-9a-f]{40}', source_sha), 'invalid source SHA')
  require(command('git', '-C', LIVE, 'rev-parse', 'HEAD') == BASELINE, 'running version differs from rollback baseline')
  require(command('git', '-C', LIVE, 'rev-parse', 'refs/heads/lean-release') == BASELINE, 'rollback branch moved')
  if state_path.exists():
    previous = json.loads(state_path.read_text())
    if previous['phase'] != 'rolled-back':
      require(previous['phase'] == 'prepared' and previous['source_sha'] == source_sha, 'finish rollback before starting another deployment')
      updates.write(b'1')
      return
  original = updates.read()
  state = {'source_sha': source_sha, 'original_commit': BASELINE, 'phase': 'prepared',
           'disable_updates': original.hex() if original is not None else None}
  save(state_path, state)
  updates.write(b'1')


def verify_tree(command, sha):
  require(command('git', '-C', LIVE, 'rev-parse', 'HEAD') == sha, 'HEAD verification failed')
  command('git', '-C', LIVE, 'diff', '--quiet', 'HEAD', '--')


def switch(action, source_sha, release_sha, updates, state_path=STATE, *, command=run):
  state = json.loads(state_path.read_text())
  changed = False
  try:
    require(state['original_commit'] == BASELINE, 'rollback baseline mismatch')
    if action == 'consume':
      require(updates.read() == b'1', 'DisableUpdates must remain 1 during deployment')
      require(state['phase'] == 'prepared' and state['source_sha'] == source_sha, 'source SHA/state mismatch')
      require(re.fullmatch('[0-9a-f]{40}', release_sha), 'invalid release SHA')
      command('git', '-C', LIVE, 'fetch', '-q', STAGE, 'refs/heads/nav-local-release')
      require(command('git', '-C', LIVE, 'rev-parse', 'FETCH_HEAD') == release_sha, 'fetched release SHA mismatch')
      message = command('git', '-C', LIVE, 'log', '-1', '--format=%B', 'FETCH_HEAD')
      require(f'source commit: nav-test@{source_sha}' in message.splitlines(), 'source commit message mismatch')
      target, branch = release_sha, 'nav-local-release'
    else:
      require(action == 'rollback', 'unknown operation')
      require(command('git', '-C', LIVE, 'rev-parse', 'refs/heads/lean-release') == BASELINE, 'rollback branch moved')
      updates.write(b'1')
      target, branch = BASELINE, 'lean-release'
    state['phase'] = 'switching'
    save(state_path, state)
    command('sudo', 'systemctl', 'stop', 'comma')
    changed = True
    command('git', '-C', LIVE, 'checkout', '-q', '--force', '-B', branch, target)
    verify_tree(command, target)
    if action == 'rollback':
      restore_updates(updates, state)
    if action == 'consume':
      state['release_sha'] = release_sha
    state['phase'] = 'rebooting' if action == 'consume' else 'rolled-back'
    save(state_path, state)
    command('sudo', 'reboot')
  except BaseException:
    try:
      if changed:
        command('git', '-C', LIVE, 'checkout', '-q', '--force', '-B', 'lean-release', BASELINE)
      verify_tree(command, BASELINE)
      restore_updates(updates, state)
      state['phase'] = 'recovered'
    except BaseException as recovery_error:
      state['phase'] = 'failed'
      print(f'代码/参数恢复失败；不重启，请检查现场：{recovery_error}', file=sys.stderr, flush=True)
    finally:
      try:
        save(state_path, state)
      finally:
        try:
          command('sudo', 'systemctl', 'start', 'comma')
        finally:
          print(f'不重启。设备端回滚命令：{ROLLBACK}', file=sys.stderr, flush=True)
    raise


def main():
  import fcntl
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=['prepare', 'consume', 'rollback', 'status'])
  parser.add_argument('source_sha', nargs='?')
  parser.add_argument('release_sha', nargs='?')
  args = parser.parse_args()
  if args.action == 'status':
    print(json.loads(STATE.read_text())['phase'])
    return
  with STATE.with_suffix('.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    flag = UpdateFlag()
    if args.action == 'prepare':
      prepare(args.source_sha or '', flag)
    else:
      switch(args.action, args.source_sha, args.release_sha, flag)


if __name__ == '__main__':
  main()
