#!/usr/bin/env python3
"""Build a public encrypted Sienna package and a separately stored private key record."""
import argparse
import base64
from datetime import date
from pathlib import Path
import secrets
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'opendbc_repo'))
from Crypto.Cipher import AES
import zstandard
from opendbc import DBC_PATH, get_generated_dbcs
from opendbc.can.dbc_asset import CHUNK_SIZE, DOMAIN, FORMAT, HEADER, MAGIC, PLATFORM, SOURCES, json_bytes, parse_manifest, private_write, sha256
from opendbc.car import Bus
from opendbc.car.toyota.values import CAR, DBC

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def validate_source(source_sha):
  def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True, stderr=subprocess.PIPE).strip()

  try:
    head = git(SOURCE_ROOT, 'rev-parse', 'HEAD')
    if source_sha != head or head != git(SOURCE_ROOT, 'rev-parse', 'refs/heads/lean-master^{commit}'):
      raise ValueError('source SHA must match the actual lean-master HEAD')
    link = git(SOURCE_ROOT, 'ls-tree', 'HEAD', 'opendbc_repo').split()
    if len(link) != 4 or link[:2] != ['160000', 'commit'] or link[2] != git(SOURCE_ROOT / 'opendbc_repo', 'rev-parse', 'HEAD'):
      raise ValueError('opendbc HEAD must match the source gitlink')
    for path in (SOURCE_ROOT, SOURCE_ROOT / 'opendbc_repo'):
      if git(path, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=none'):
        raise ValueError('source and opendbc working trees must be clean')
  except subprocess.CalledProcessError:
    raise ValueError('source Git identity is unavailable') from None


def build_package(output: Path, private_key_path: Path, source_sha: str, data_date: str):
  output, private_key_path = output.resolve(), private_key_path.resolve()
  if output == private_key_path or output in private_key_path.parents:
    raise ValueError('private key must be outside public output')
  if private_key_path.exists() or (output.exists() and any(output.iterdir())):
    raise ValueError('refusing to overwrite package or private key')
  validate_source(source_sha)
  generated = get_generated_dbcs()
  dependencies = DBC[CAR.TOYOTA_SIENNA_PATCHED_LTA]
  contents = {}
  for (name, source), bus in zip(SOURCES.items(), (Bus.pt, Bus.radar), strict=True):
    if dependencies[bus] not in (name, source):
      raise ValueError('unrecognized Sienna DBC dependency')
    content = generated[source].encode() if source in generated else (Path(DBC_PATH) / (source + '.dbc')).read_bytes()
    contents[name] = content
  validate_source(source_sha)
  raw, encrypted, record = encode_package(contents, source_sha, data_date)
  private_write(private_key_path, json_bytes(record))
  output.mkdir(parents=True, exist_ok=True)
  (output / 'package.enc').write_bytes(encrypted)
  (output / 'manifest.json').write_bytes(raw)
  return parse_manifest(raw)


def encode_package(contents: dict[str, bytes], source_sha: str, data_date: str):
  """Manifest/encoding seam; source authorization belongs to the build entry point."""
  if set(contents) != set(SOURCES):
    raise ValueError('exactly the two Sienna DBC assets are required')
  assets = [{'name': name, 'source': source, 'size': len(contents[name]), 'sha256': sha256(contents[name])}
            for name, source in SOURCES.items()]
  plaintext = b''.join(contents[name] for name in SOURCES)
  compressed = zstandard.ZstdCompressor(level=19, write_content_size=True, write_checksum=True, threads=0).compress(plaintext)
  key, key_id, file_id = secrets.token_bytes(32), secrets.token_hex(16), secrets.token_bytes(4)
  header = HEADER.pack(MAGIC, bytes.fromhex(key_id), file_id, len(compressed))
  encrypted = bytearray(header)
  for counter, offset in enumerate(range(0, len(compressed), CHUNK_SIZE)):
    block = compressed[offset:offset + CHUNK_SIZE]
    cipher = AES.new(key, AES.MODE_GCM, nonce=file_id + counter.to_bytes(8, 'big'), mac_len=16)
    cipher.update(DOMAIN + header + struct.pack('>QI', counter, len(block)))
    ciphertext, tag = cipher.encrypt_and_digest(block)
    encrypted.extend(ciphertext + tag)
  encrypted = bytes(encrypted)
  manifest = {'format': FORMAT, 'namespace': 'dbc', 'id': source_sha + '-' + key_id, 'platform': PLATFORM,
              'source_sha': source_sha, 'key_id': key_id, 'data_date': data_date, 'chunk_size': CHUNK_SIZE, 'tag_size': 16,
              'nonce_scheme': 'file-id32-counter64-be',
              'package': {'file': 'package.enc', 'size': len(encrypted), 'sha256': sha256(encrypted)},
              'plaintext': {'size': len(plaintext), 'sha256': sha256(plaintext)}, 'assets': assets}
  raw = json_bytes(manifest)
  parse_manifest(raw)
  record = {'namespace': 'dbc', 'id': manifest['id'], 'key_format': 1, 'algorithm': 'AES-256-GCM', 'key_id': key_id,
            'data_date': data_date, 'manifest_sha256': sha256(raw), 'key_wrap': 'none', 'key_b64': base64.b64encode(key).decode()}
  return raw, encrypted, record


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--private-key', type=Path, required=True)
  parser.add_argument('--source-sha', required=True)
  parser.add_argument('--data-date', default=date.today().isoformat())
  args = parser.parse_args()
  build_package(args.output, args.private_key, args.source_sha, args.data_date)
