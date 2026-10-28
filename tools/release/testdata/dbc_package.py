"""Explicit test package adapter. The production builder always verifies Git source identity."""
from pathlib import Path
import json

from opendbc import DBC_PATH, get_generated_dbcs
from opendbc.can.dbc_asset import SOURCES, json_bytes, private_write
from tools.release.dbc_package import encode_package


def build_test_package(output: Path, private: Path, revision: str, data_date: str):
  generated = get_generated_dbcs()
  contents = {name: generated[source].encode() if source in generated else (Path(DBC_PATH) / (source + '.dbc')).read_bytes()
              for name, source in SOURCES.items()}
  raw, encrypted, record = encode_package(contents, revision, data_date)
  output.mkdir(parents=True)
  (output / 'manifest.json').write_bytes(raw)
  (output / 'package.enc').write_bytes(encrypted)
  private_write(private, json_bytes(record))
  return json.loads(raw)
