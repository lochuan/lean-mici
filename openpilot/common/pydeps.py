"""Offline Sanic sets, installed once and selected by the local release manifest."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

PACKAGES = {name.replace('_', '-'): name for name in (
  'aiofiles', 'html5tagger', 'httptools', 'multidict', 'sanic', 'sanic_routing',
  'setuptools', 'tracerite', 'typing_extensions', 'ujson', 'uvloop', 'websockets')}
MANIFEST = 'RELEASE_MANIFEST.json'
ROOT = Path('/data/pydeps')
# shortcut: Sanic's current closure is 25 MiB; review this unpack limit when the lock changes.
MAX_UNPACKED_BYTES = 64 * 1024 * 1024


def require(condition, message):
  if not condition:
    raise ValueError(message)


def canonical(value):
  return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha256(data):
  return hashlib.sha256(data).hexdigest()


def file_hash(path):
  with Path(path).open('rb') as stream:
    return hashlib.file_digest(stream, 'sha256').hexdigest()


def dependency_id(deps):
  return sha256(canonical([deps['archive_sha256'], deps['packages']]))


def load_manifest(application, *, source_sha=None):
  manifest = json.loads((Path(application) / MANIFEST).read_text())
  require(re.fullmatch('[0-9a-f]{64}', manifest['pydeps']['id']), 'invalid dependency id')
  if source_sha is not None:
    require(manifest['source_commit'] == source_sha, 'dependency source mismatch')
  return manifest


def dependency_path(application, root=ROOT):
  dependency = Path(root) / 'sets' / load_manifest(application)['pydeps']['id']
  require(not dependency.is_symlink() and not (dependency / 'site-packages').is_symlink(), 'symlinked dependency set')
  require((dependency / 'COMPLETE').read_text() == dependency.name and (dependency / 'site-packages').is_dir(), 'incomplete dependency set')
  return dependency.resolve() / 'site-packages'


def verify_archive(application, manifest):
  deps = manifest['pydeps']
  require(deps['id'] == dependency_id(deps), 'dependency identity mismatch')
  archive = Path(application) / (deps['id'] + '.tar.gz')
  require(archive.stat().st_size == deps['compressed_bytes'] and file_hash(archive) == deps['archive_sha256'], 'archive hash/size mismatch')
  return archive


def probe(site, packages, *, command=subprocess.run):
  script = '''
import importlib, importlib.metadata, json, pathlib, sys
site = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(site))
packages, modules = json.loads(sys.argv[2]), json.loads(sys.argv[3])
assert len(packages) == len(modules) and {p['name'] for p in packages} == set(modules)
actual = {d.metadata['Name'].lower().replace('_', '-'): d.version for d in importlib.metadata.distributions(path=[str(site)])}
assert actual == {p['name']: p['version'] for p in packages}, ('distribution closure', actual)
for name in ['setuptools'] + [p['name'] for p in packages if p['name'] != 'setuptools']:
  module = importlib.import_module(modules[name])
  assert pathlib.Path(module.__file__).resolve().is_relative_to(site), (name, module.__file__)
'''
  command([sys.executable, '-I', '-B', '-c', script, str(site), json.dumps(packages), json.dumps(PACKAGES)],
          timeout=30, check=True, capture_output=True, text=True)


def _fsync(path):
  descriptor = os.open(path, os.O_RDONLY)
  try:
    os.fsync(descriptor)
  finally:
    os.close(descriptor)


def install(application, root=ROOT, *, source_sha=None, command=subprocess.run):
  manifest = load_manifest(application, source_sha=source_sha)
  deps = manifest['pydeps']
  archive = verify_archive(application, manifest)
  root = Path(root)
  root.mkdir(parents=True, exist_ok=True)
  with os.fdopen(os.open(root / '.install.lock', os.O_CREAT | os.O_RDWR, 0o600), 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    destination = root / 'sets' / deps['id']
    if destination.exists():
      site = dependency_path(application, root)
      probe(site, deps['packages'], command=command)
      return site
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=root) as temporary:
      candidate = Path(temporary)
      with tarfile.open(archive, 'r:gz') as stream:
        seen = set()
        unpacked_bytes = 0
        for member in stream:
          name = member.name
          require(member.isfile() and name.startswith('site-packages/') and '\\' not in name
                  and all(part not in ('', '.', '..') for part in name.split('/')) and name not in seen, 'unsafe archive member')
          seen.add(name)
          unpacked_bytes += member.size
          require(unpacked_bytes <= MAX_UNPACKED_BYTES, 'unpacked archive limit exceeded')
          stream.extract(member, candidate, filter='data')
      probe(candidate / 'site-packages', deps['packages'], command=command)
      with (candidate / 'COMPLETE').open('x') as marker:
        marker.write(deps['id'])
        marker.flush()
        os.fsync(marker.fileno())
      for path in candidate.rglob('*'):
        if path.is_file():
          path.chmod(0o444)
          _fsync(path)
      for path in sorted((p for p in candidate.rglob('*') if p.is_dir()), reverse=True):
        path.chmod(0o555)
        _fsync(path)
      _fsync(candidate)
      os.rename(candidate, destination)
      destination.chmod(0o555)
      _fsync(destination)
      _fsync(destination.parent)
      return destination.resolve() / 'site-packages'


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('path', 'install'))
  parser.add_argument('application', type=Path)
  parser.add_argument('--root', type=Path, default=ROOT)
  args = parser.parse_args()
  try:
    print(install(args.application, args.root) if args.action == 'install' else dependency_path(args.application, args.root))
  except Exception as error:
    print(f'pydeps: {getattr(error, "stderr", None) or str(error)}', file=sys.stderr)
    return 1
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
