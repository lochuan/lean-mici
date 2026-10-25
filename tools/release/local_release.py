#!/usr/local/venv/bin/python
"""Device-side local release consume/rollback. Launch mutations via setsid nohup."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# The copied worker imports its adjacent runtime helper, including on legacy apps.
sys.path.insert(0, str(Path(__file__).parent if Path(__file__).with_name('pydeps.py').exists()
                       else Path(__file__).resolve().parents[2] / 'openpilot/common'))
import pydeps

LIVE = Path('/data/openpilot')
STAGE = Path('/data/relstage')
STATE = Path('/data/build/.git/local-release-state.json')
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


def prepare(source_sha, updates, state_path=STATE, *, source_ref='refs/heads/nav-test', command=run, payload=None,
            dependency_root=pydeps.ROOT, probe_command=None):
  require(re.fullmatch('[0-9a-f]{40}', source_sha), 'invalid source SHA')
  require(re.fullmatch(r'refs/heads/[A-Za-z0-9_][A-Za-z0-9_./-]*', source_ref), 'invalid source ref')
  command('git', 'check-ref-format', source_ref)
  source_branch = source_ref.removeprefix('refs/heads/')
  original_commit = command('git', '-C', LIVE, 'rev-parse', 'HEAD')
  original_ref = command('git', '-C', LIVE, 'symbolic-ref', 'HEAD')
  require(original_ref.startswith('refs/heads/'), 'live HEAD must be on a local branch')
  verify_tree(command, original_commit)
  boot_id = command('cat', '/proc/sys/kernel/random/boot_id')
  if state_path.exists():
    previous = json.loads(state_path.read_text())
    if previous['phase'] == 'rebooting':
      if previous.get('boot_id'):
        require(previous['boot_id'] != boot_id, 'previous deployment has not rebooted')
      else:
        # Legacy workers recorded no boot ID; only accept state written before this boot.
        boot_started = int(command('awk', '$1 == "btime" { print $2 }', '/proc/stat'))
        require(state_path.stat().st_mtime < boot_started, 'legacy deployment has no reboot evidence')
      require(original_ref == 'refs/heads/nav-local-release' and original_commit == previous['release_sha'], 'previous release mismatch')
      message = command('git', '-C', LIVE, 'log', '-1', '--format=%B')
      require(f"source commit: {previous.get('source_branch', 'nav-test')}@{previous['source_sha']}" in message.splitlines(),
              'previous source mismatch')
      require(updates.read() == b'1', 'previous deployment updates flag changed')
      require(command('systemctl', 'is-active', 'comma') == 'active', 'previous deployment service is not active')
      command('sh', '-c', 'pgrep -f "[m]anager.py" >/dev/null && ! pgrep -f "/usr/comma/[r]eset" >/dev/null')
    elif previous['phase'] == 'rolled-back':
      require(previous['original_commit'] == original_commit and previous['original_ref'] == original_ref, 'rollback identity mismatch')
      flag = previous['disable_updates']
      require(updates.read() == (bytes.fromhex(flag) if flag is not None else None), 'rollback parameter mismatch')
      require(previous['boot_id'] != boot_id, 'previous rollback has not rebooted')
    else:
      require(previous['phase'] == 'prepared' and previous['source_sha'] == source_sha
              and previous.get('source_branch') == source_branch, 'finish rollback before starting another deployment')
      require(previous['original_commit'] == original_commit and previous['original_ref'] == original_ref
              and previous['boot_id'] == boot_id, 'prepared live identity changed')
      require(command('git', '-C', LIVE, 'rev-parse', previous['rollback_ref']) == original_commit, 'rollback ref moved')
      updates.write(b'1')
      return
  pydeps.install(Path(payload) if payload is not None else STATE.parent / 'pydeps' / source_sha, dependency_root,
                 source_sha=source_sha, command=probe_command or subprocess.run)
  original = updates.read()
  rollback_ref = f'refs/local-release/rollback/{original_commit}'
  command('git', '-C', LIVE, 'update-ref', rollback_ref, original_commit)
  state = {'source_sha': source_sha, 'source_branch': source_branch, 'original_commit': original_commit, 'original_ref': original_ref,
           'rollback_ref': rollback_ref, 'boot_id': boot_id, 'phase': 'prepared',
           'disable_updates': original.hex() if original is not None else None}
  save(state_path, state)
  updates.write(b'1')


def verify_tree(command, sha):
  require(command('git', '-C', LIVE, 'rev-parse', 'HEAD') == sha, 'HEAD verification failed')
  command('git', '-C', LIVE, 'diff', '--quiet', 'HEAD', '--')


def switch(action, source_sha, release_sha, updates, state_path=STATE, *, command=run):
  state = json.loads(state_path.read_text())
  original_commit, original_ref = state['original_commit'], state['original_ref']
  require(re.fullmatch('[0-9a-f]{40}', original_commit), 'invalid rollback SHA')
  require(original_ref.startswith('refs/heads/'), 'invalid rollback branch')
  command('git', 'check-ref-format', original_ref)
  require(command('git', '-C', LIVE, 'rev-parse', state['rollback_ref']) == original_commit, 'rollback ref moved')
  original_branch = original_ref.removeprefix('refs/heads/')
  require(action in ('consume', 'rollback'), 'unknown operation')
  if action == 'rollback' and source_sha is not None:
    require(source_sha == state['source_sha'], 'rollback source mismatch')
  if action == 'consume':
    require(state['phase'] == 'prepared' and state['source_sha'] == source_sha, 'source SHA/state mismatch')
    require(command('git', '-C', LIVE, 'rev-parse', 'HEAD') == original_commit
            and command('git', '-C', LIVE, 'symbolic-ref', 'HEAD') == original_ref, 'live identity changed after prepare')
    verify_tree(command, original_commit)
  changed = False
  try:
    if action == 'consume':
      require(updates.read() == b'1', 'DisableUpdates must remain 1 during deployment')
      require(re.fullmatch('[0-9a-f]{40}', release_sha), 'invalid release SHA')
      command('git', '-C', LIVE, 'fetch', '-q', STAGE, 'refs/heads/nav-local-release')
      require(command('git', '-C', LIVE, 'rev-parse', 'FETCH_HEAD') == release_sha, 'fetched release SHA mismatch')
      message = command('git', '-C', LIVE, 'log', '-1', '--format=%B', 'FETCH_HEAD')
      require(f"source commit: {state['source_branch']}@{source_sha}" in message.splitlines(), 'source commit message mismatch')
      target, branch = release_sha, 'nav-local-release'
    else:
      updates.write(b'1')
      target, branch = original_commit, original_branch
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
    state['boot_id'] = command('cat', '/proc/sys/kernel/random/boot_id')
    state['phase'] = 'rebooting' if action == 'consume' else 'rolled-back'
    save(state_path, state)
    command('sudo', 'reboot')
  except BaseException:
    try:
      if changed:
        command('git', '-C', LIVE, 'checkout', '-q', '--force', '-B', original_branch, original_commit)
      verify_tree(command, original_commit)
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
  parser.add_argument('--source-ref', default='refs/heads/nav-test')
  args = parser.parse_args()
  if args.action == 'status':
    print(json.loads(STATE.read_text())['phase'])
    return
  with STATE.with_suffix('.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    flag = UpdateFlag()
    if args.action == 'prepare':
      prepare(args.source_sha or '', flag, source_ref=args.source_ref)
    else:
      switch(args.action, args.source_sha, args.release_sha, flag)


if __name__ == '__main__':
  main()
