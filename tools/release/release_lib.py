#!/usr/bin/env python3
"""Helpers for the device-built lean release (called by device_release.sh).

The release tree is the clean build tree (/data/build) stripped to runtime
files. Its completeness comes from the build itself (a full ``scons`` in a
tree that ``git clean`` resets to the source commit), so this module only
owns what the stripped tree cannot derive on its own: the tinygrad pin, and
the publish gate on the stage tree.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from openpilot.common import pydeps

TINYGRAD_PIN_FILE = "TINYGRAD_PIN"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# GitHub rejects files over 100 MB; stay under it with the same margin as upstream's build_stripped.sh.
MAX_FILE_BYTES = 95 * 1024 * 1024


def stamp_tinygrad_pin(stage: Path, source_repo: Path) -> str:
  """Record the tinygrad_repo revision pinned by ``source_repo``'s HEAD into the staged tree.

  The published tree strips tinygrad_repo/.git, and the model selector's runtime
  gating (openpilot/sunnypilot/models/tinygrad_ref.py) falls back to this file.
  The pin is read from the gitlink (``git ls-tree``), the source of truth.
  """
  result = subprocess.run(["git", "-C", str(source_repo), "ls-tree", "HEAD", "tinygrad_repo"],
                          check=True, capture_output=True, text=True)
  fields = result.stdout.split()
  if len(fields) < 3 or fields[0] != "160000" or fields[1] != "commit":
    raise ValueError(
      f"tinygrad_repo is not a gitlink in {source_repo}: {result.stdout.strip()!r}")
  sha = fields[2]
  dest = stage / "tinygrad_repo" / TINYGRAD_PIN_FILE
  dest.parent.mkdir(parents=True, exist_ok=True)
  dest.write_text(sha + "\n")
  return sha


def read_stage_tinygrad_pin(stage: Path) -> str | None:
  """Resolve the staged tree's tinygrad pin; None when absent or malformed."""
  try:
    sha = (stage / "tinygrad_repo" / TINYGRAD_PIN_FILE).read_text().strip()
  except OSError:
    return None
  return sha if _SHA_RE.match(sha) else None


def check_stage(stage: Path, source_sha: str | None = None) -> list[str]:
  """Publish gate on the stage tree; returns problems, empty = pass."""
  problems = []
  try:
    stamped_sha = (stage / 'SOURCE_SHA').read_text().strip()
    if not _SHA_RE.fullmatch(stamped_sha) or (source_sha is not None and stamped_sha != source_sha):
      raise ValueError('SOURCE_SHA mismatch or invalid')
    manifest = pydeps.load_manifest(stage, source_sha=stamped_sha)
    pydeps.verify_archive(stage, manifest)
    lock = stage / 'uv.lock'
    if pydeps.file_hash(lock) != manifest['uv_lock_sha256']:
      problems.append('release manifest lock hash mismatch')
  except (OSError, ValueError, KeyError, TypeError) as error:
    problems.append(f'release manifest / SOURCE_SHA rejected: {error}')
  if read_stage_tinygrad_pin(stage) is None:
    problems.append("stage tree cannot resolve its tinygrad pin")
  if (stage / 'opendbc_repo/opendbc/car/tests/dbc_adapter.py').exists():
    problems.append('stage must not ship the test DBC adapter')
  for p in sorted(stage.rglob("*")):
    if p.is_file() and not p.is_symlink() and p.stat().st_size > MAX_FILE_BYTES:
      problems.append(f"file exceeds GitHub's size limit: {p.relative_to(stage)}")
  return problems


def dbc_needs_build(download_base_url: str, source_sha: str) -> bool:
  """Empty or source-mismatched R2 metadata requires a package; approved sources pin opendbc."""
  if not _SHA_RE.fullmatch(source_sha):
    raise ValueError('source SHA must contain exactly 40 lowercase hex characters')
  if not download_base_url.startswith('https://'):
    raise ValueError('DOWNLOAD_BASE_URL must use HTTPS')
  url = download_base_url.rstrip('/') + '/dbc/sienna-patched-lta/latest.json'
  try:
    with urllib.request.urlopen(url, timeout=15) as response:
      raw = response.read(16385)
  except urllib.error.HTTPError as error:
    if error.code == 404:
      return True
    raise ValueError('R2 DBC latest check failed') from None
  except (urllib.error.URLError, TimeoutError, OSError):
    raise ValueError('R2 DBC latest check failed') from None
  if len(raw) > 16384:
    raise ValueError('R2 DBC latest is too large')
  try:
    latest = json.loads(raw)
  except (ValueError, TypeError, RecursionError):
    return True
  # A changed source SHA covers gitlink/DBC changes and is required by the runtime source gate.
  if not isinstance(latest, dict) or latest.get('source_sha') != source_sha:
    return True
  identifier = latest.get('id')
  if not isinstance(identifier, str) or not re.fullmatch(source_sha + '-[0-9a-f]{32}', identifier):
    return True
  base = download_base_url.rstrip('/') + '/dbc/'
  paths = ('sienna-patched-lta/' + source_sha + '/latest.json',
           identifier + '/manifest.json', identifier + '/package.enc')
  for path in paths:
    try:
      with urllib.request.urlopen(urllib.request.Request(base + path, method='HEAD'), timeout=15):
        pass
    except urllib.error.HTTPError as error:
      if error.code == 404:
        return True
      raise ValueError('R2 DBC artifact check failed') from None
    except (urllib.error.URLError, TimeoutError, OSError):
      raise ValueError('R2 DBC artifact check failed') from None
  return False


def send_local_source(source_repo: Path, device: str, expected_source_sha: str, source_ref: str = 'refs/heads/nav-test') -> str:
  """Stage an approved local branch and its gitlinks, without public remotes."""
  def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source_repo), *args], text=True).strip()

  if not _SHA_RE.fullmatch(expected_source_sha):
    raise ValueError("EXPECTED_SOURCE_SHA must contain exactly 40 lowercase hex characters")
  if not re.fullmatch(r'refs/heads/[A-Za-z0-9_][A-Za-z0-9_./-]*', source_ref):
    raise ValueError('source ref must be a fully qualified local branch')
  git('check-ref-format', source_ref)
  revision = git("rev-parse", source_ref + "^{commit}")
  if revision != expected_source_sha:
    raise ValueError(f"EXPECTED_SOURCE_SHA mismatch: expected {expected_source_sha}, {source_ref} is {revision}")
  common = Path(git("rev-parse", "--git-common-dir"))
  if not common.is_absolute():
    common = source_repo / common
  common = common.resolve()
  sources = [(common, revision)]
  paths = git("config", "--blob", f"{revision}:.gitmodules", "--get-regexp", r"\.path$")
  for line in paths.splitlines():
    key, path = line.split(None, 1)
    name = key[len("submodule."):-len(".path")]
    sources.append((common / "modules" / name, git("rev-parse", f"{revision}:{path}")))

  # Prepare every dependency before writing the device; missing local objects fail here.
  with tempfile.TemporaryDirectory(prefix="local-release-", dir="/tmp") as temporary:
    payloads = []
    for source_git, sha in sources:
      bare = Path(temporary) / sha
      subprocess.run(["git", "init", "-q", "--bare", "--template=", str(bare)], check=True)
      subprocess.run(["git", "-C", str(bare), "fetch", "-q", "--depth=1", source_git.as_uri(),
                      f"{sha}:refs/heads/source"], check=True)
      archive = Path(temporary) / f"{sha}.tar"
      with tarfile.open(archive, "w") as stream:
        stream.add(bare, arcname=".")
      payloads.append((sha, archive))
    cache_script = """
import json, pathlib, re, shutil, sys
root = pathlib.Path('/data/build/.git/local-source')
root.mkdir(parents=True, exist_ok=True)
index = root / 'generations.json'
old = json.loads(index.read_text()) if index.exists() else {}
current, commits = sys.argv[1], json.loads(sys.argv[2])
previous = old.get('previous', []) if old.get('current') == current else old.get('commits', [])
keep = set(commits + previous)
for path in root.iterdir():
  if path.is_dir() and re.fullmatch('[0-9a-f]{40}', path.name) and path.name not in keep:
    shutil.rmtree(path)
index.write_text(json.dumps({'current': current, 'commits': commits, 'previous': previous}))
"""
    subprocess.run(["ssh", device, "git init -q /data/build && " + shlex.join(
      ["/usr/local/venv/bin/python", "-c", cache_script, revision, json.dumps([sha for sha, _ in payloads])])],
      stdout=subprocess.DEVNULL, check=True)
    for sha, archive in payloads:
      destination = shlex.quote(f"/data/build/.git/local-source/{sha}")
      command = " && ".join([
        "git init -q /data/build",
        "git -C /data/build config remote.origin.url https://github.com/lochuan/lean-mici.git",
        f"mkdir -p {destination}",
        f"tar -xf - -C {destination}",
        f"git --git-dir={destination} cat-file -e {sha}^{{commit}}",
      ])
      with archive.open("rb") as stream:
        subprocess.run(["ssh", device, command], stdin=stream, stdout=subprocess.DEVNULL, check=True)
  return revision


def send_local_pydeps(source_repo: Path, device: str, source_sha: str, payload: Path) -> None:
  manifest = pydeps.load_manifest(payload, source_sha=source_sha)
  lock = subprocess.check_output(['git', '-C', str(source_repo), 'show', f'{source_sha}:uv.lock'])
  pydeps.require(pydeps.sha256(lock) == manifest['uv_lock_sha256'], 'dependency manifest source lock mismatch')
  archive = pydeps.verify_archive(payload, manifest)
  destination = f'/data/build/.git/pydeps/{source_sha}'
  script = """
set -eu
mkdir -p /data/build/.git/pydeps
incoming=$(mktemp -d /data/build/.git/pydeps/.incoming.XXXXXX)
tar -xf - -C "$incoming"
if [ -e "$1" ]; then
  cmp "$incoming/RELEASE_MANIFEST.json" "$1/RELEASE_MANIFEST.json"
  cmp "$incoming/$2" "$1/$2"
  rm -r "$incoming"
else
  mv "$incoming" "$1"
fi
"""
  with tempfile.TemporaryFile() as stream:
    with tarfile.open(fileobj=stream, mode='w') as bundle:
      bundle.add(payload / pydeps.MANIFEST, arcname=pydeps.MANIFEST)
      bundle.add(archive, arcname=archive.name)
    stream.seek(0)
    subprocess.run(
      ['ssh', device, shlex.join(['bash', '-c', script, 'pydeps-transfer', destination, archive.name])], stdin=stream, check=True, stdout=subprocess.DEVNULL
    )


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  sub = parser.add_subparsers(dest="command", required=True)
  stamp = sub.add_parser("stamp-tinygrad-pin", help="write the tinygrad pin from the source gitlink into the stage")
  stamp.add_argument("stage")
  stamp.add_argument("source_repo")
  check = sub.add_parser("check-stage", help="publish gate: tinygrad pin resolvable, no oversized files")
  check.add_argument("stage")
  check.add_argument("--source-sha")
  local = sub.add_parser("send-local-source", help="stage an approved local branch and pinned submodules over SSH")
  local.add_argument("source_repo", type=Path)
  local.add_argument("device")
  local.add_argument("expected_source_sha")
  local.add_argument("--source-ref", default='refs/heads/nav-test')
  deps = sub.add_parser('send-local-pydeps', help='transfer a source-bound offline dependency artifact')
  deps.add_argument('source_repo', type=Path)
  deps.add_argument('device')
  deps.add_argument('source_sha')
  deps.add_argument('payload', type=Path)
  dbc = sub.add_parser('dbc-needs-build', help='check whether R2 lacks the DBC manifest for this approved source')
  dbc.add_argument('download_base_url')
  dbc.add_argument('source_sha')
  args = parser.parse_args()

  if args.command == 'dbc-needs-build':
    print('1' if dbc_needs_build(args.download_base_url, args.source_sha) else '0')
    return 0

  if args.command == 'send-local-pydeps':
    send_local_pydeps(args.source_repo, args.device, args.source_sha, args.payload)
    return 0

  if args.command == "send-local-source":
    print(send_local_source(args.source_repo, args.device, args.expected_source_sha, args.source_ref))
    return 0

  if args.command == "stamp-tinygrad-pin":
    sha = stamp_tinygrad_pin(Path(args.stage), Path(args.source_repo))
    print(f"[ok] tinygrad pin stamped: {sha[:7]}")
    return 0

  problems = check_stage(Path(args.stage), args.source_sha)
  for problem in problems:
    print(problem, file=sys.stderr)
  return 1 if problems else 0


if __name__ == "__main__":
  raise SystemExit(main())
