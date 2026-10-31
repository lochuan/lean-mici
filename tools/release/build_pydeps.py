#!/usr/bin/env python3
"""Build a locked Sanic runtime in the pinned Linux/arm64 CPython 3.12 builder.

Offline by default. Wheel acquisition is an explicit build-time --download step.
The device only consumes the resulting archive through openpilot.common.pydeps.
"""

import argparse
import email
import gzip
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

# The runtime helper ships with the application; builder and launcher share its rules.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openpilot.common import pydeps

# packaging is already pinned in uv.lock and provided by the builder.
from packaging.markers import Marker
from packaging.requirements import Requirement

TARGET = {'implementation_name': 'cpython', 'implementation_version': '3.12.3', 'os_name': 'posix',
          'platform_machine': 'aarch64', 'platform_python_implementation': 'CPython', 'platform_release': '',
          'platform_system': 'Linux', 'platform_version': '', 'python_full_version': '3.12.3',
          'python_version': '3.12', 'sys_platform': 'linux', 'extra': ''}


def select_wheels(lock):
  packages = {p['name']: p for p in tomllib.loads(lock.decode())['package']}
  visited = set()

  def visit(name):
    if name in visited:
      return
    visited.add(name)
    for dep in packages[name].get('dependencies', []):
      if not dep.get('marker') or Marker(dep['marker']).evaluate(TARGET):
        visit(dep['name'])

  visit('sanic')
  pydeps.require(visited == set(pydeps.PACKAGES), 'locked Sanic closure changed; review target packaging')
  selected = []
  for name in sorted(visited):
    package = packages[name]
    wheels = [w for w in package['wheels'] if '-cp312-cp312-manylinux2014_aarch64.' in w['url']]
    if not wheels:
      wheels = [w for w in package['wheels'] if w['url'].endswith('-py3-none-any.whl')]
    pydeps.require(len(wheels) == 1, f'no unique audited Linux arm64 cp312 wheel: {name}')
    wheel = wheels[0]
    selected.append({'name': name, 'version': package['version'], 'wheel': Path(urlparse(wheel['url']).path).name,
                     'sha256': wheel['hash'].removeprefix('sha256:'), 'url': wheel['url']})
  return selected


def build(source_repo, source_sha, wheels, output, *, download=False):
  lock = subprocess.check_output(['git', '-C', str(source_repo), 'show', f'{source_sha}:uv.lock'])
  selected = select_wheels(lock)
  pydeps.require(not output.exists(), 'output directory already exists')
  wheels.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix='build-pydeps-', dir='/tmp') as temporary:
    work = Path(temporary)
    wheelhouse = work / 'wheels'
    wheelhouse.mkdir()
    for package in selected:
      path = wheels / package['wheel']
      if not path.exists() and download:
        pydeps.require(urlparse(package['url']).scheme == 'https' and urlparse(package['url']).hostname == 'files.pythonhosted.org', 'unexpected wheel source')
        with urlopen(package['url'], timeout=60) as response, path.with_suffix('.partial').open('wb') as stream:
          shutil.copyfileobj(response, stream)
        pydeps.require(pydeps.file_hash(path.with_suffix('.partial')) == package['sha256'], 'downloaded wheel hash mismatch')
        path.with_suffix('.partial').replace(path)
      pydeps.require(pydeps.file_hash(path) == package['sha256'], f'wheel hash mismatch: {path.name}')
      shutil.copyfile(path, wheelhouse / path.name)
      with zipfile.ZipFile(path) as archive:
        metadata_paths = [n for n in archive.namelist() if n.count('/') == 1 and n.endswith('.dist-info/METADATA')]
        pydeps.require(len(metadata_paths) == 1, 'invalid wheel metadata')
        metadata = email.message_from_bytes(archive.read(metadata_paths[0]))
        pydeps.require(
          metadata['Name'].lower().replace('_', '-') == package['name'] and metadata['Version'] == package['version'], 'wheel metadata identity mismatch'
        )
        for text in metadata.get_all('Requires-Dist', []):
          requirement = Requirement(text)
          if requirement.marker is None or requirement.marker.evaluate(TARGET):
            dependency = next((p for p in selected if p['name'] == requirement.name.lower().replace('_', '-')), None)
            pydeps.require(dependency is not None and requirement.specifier.contains(dependency['version']), f'incomplete closure: {text}')
    requirements = work / 'requirements.txt'
    requirements.write_text(''.join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in selected))
    candidate = work / 'set'
    site = candidate / 'site-packages'
    subprocess.run([sys.executable, '-I', '-m', 'pip', '--disable-pip-version-check', 'install', '--no-index',
                    '--find-links', str(wheelhouse), '--require-hashes', '--only-binary=:all:', '--no-compile', '--no-deps',
                    '--target', str(site), '-r', str(requirements)], check=True)
    packages = [{key: p[key] for key in ('name', 'version', 'wheel', 'sha256')} for p in selected]
    pydeps.probe(site, packages)
    output.mkdir(parents=True)
    archive_path = output / 'bundle.tar.gz'
    with archive_path.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
      with tarfile.open(fileobj=compressed, mode='w') as archive:
        for path in sorted(p for p in candidate.rglob('*') if p.is_file()):
          info = archive.gettarinfo(str(path), arcname=path.relative_to(candidate).as_posix())
          info.uid = info.gid = info.mtime = 0
          info.uname = info.gname = ''
          info.mode = 0o444
          with path.open('rb') as stream:
            archive.addfile(info, stream)
    deps = {'archive_sha256': pydeps.file_hash(archive_path), 'compressed_bytes': archive_path.stat().st_size, 'packages': packages}
    deps['id'] = pydeps.dependency_id(deps)
    archive_path.rename(output / (deps['id'] + '.tar.gz'))
    manifest = {'source_commit': source_sha, 'uv_lock_sha256': pydeps.sha256(lock), 'pydeps': deps}
    (output / pydeps.MANIFEST).write_bytes(pydeps.canonical(manifest))
    shutil.copyfile(requirements, output / 'requirements.txt')
    return manifest


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('source_repo', type=Path)
  parser.add_argument('source_sha')
  parser.add_argument('--wheels', required=True, type=Path)
  parser.add_argument('--output', required=True, type=Path)
  parser.add_argument('--download', action='store_true')
  args = parser.parse_args()
  build(args.source_repo, args.source_sha, args.wheels, args.output, download=args.download)


if __name__ == '__main__':
  main()
