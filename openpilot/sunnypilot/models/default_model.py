import argparse
import os
import hashlib
import requests
import re

from openpilot.common.basedir import BASEDIR
from openpilot.sunnypilot import get_file_hash
from openpilot.sunnypilot.models.model_name import DEFAULT_MODEL
from openpilot.sunnypilot.models.fetcher import ModelFetcher


def get_default_model() -> str:
  return DEFAULT_MODEL


DEFAULT_MODEL_NAME_PATH = os.path.join(BASEDIR, "openpilot", "sunnypilot", "models", "model_name.py")
MODEL_HASH_PATH = os.path.join(BASEDIR, "openpilot", "sunnypilot", "models", "tests", "model_hash")
SUPERCOMBO_ONNX_PATH = os.path.join(BASEDIR, "openpilot", "selfdrive", "modeld", "models", "driving_supercombo.onnx")


def _read_model_name_fields():
  with open(DEFAULT_MODEL_NAME_PATH) as f:
    content = f.read()
  fields = {}
  for line in content.splitlines():
    if "=" in line:
      key, val = line.split("=", 1)
      fields[key.strip()] = val.strip().strip('"')
  return fields


def update_model_hash():
  fields = _read_model_name_fields()
  supercombo_hash = get_file_hash(SUPERCOMBO_ONNX_PATH)
  fingerprint = f"{supercombo_hash}:{fields.get('DEFAULT_MODEL', '')}:{fields.get('DEFAULT_MODEL_REF', '')}"
  combined_hash = hashlib.sha256(fingerprint.encode()).hexdigest()

  with open(MODEL_HASH_PATH, "w") as f:
    f.write(combined_hash)

  print(f"Generated and updated new combined model hash to {MODEL_HASH_PATH}")


def get_ref_for_name(url: str, name: str) -> str:
  response = requests.get(url, timeout=10)
  if response.status_code == 200:
    bundles = response.json()["bundles"]
    matching = [b for b in bundles if re.search(name, f"{b['short_name']} {b['display_name']}", re.IGNORECASE)]
    if matching:
      return max(matching, key=lambda b: int(b["index"]))["ref"]
  return ""


def update_default_model_names(default_model_name: str):
  print("[CHANGE DEFAULT MODEL NAME]")
  small_ref = get_ref_for_name(ModelFetcher.MODEL_URL, default_model_name)

  with open(DEFAULT_MODEL_NAME_PATH, "w") as f:
    f.write(f'DEFAULT_MODEL = "{default_model_name}"\n')
    f.write(f'DEFAULT_MODEL_REF = "{small_ref}"\n')

  print(f'New default model name: "{default_model_name}" (ref: {small_ref})')
  print("[DONE]")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Update default model name and hash")
  parser.add_argument("--new_small_model_name", type=str, help="New default model name")
  args = parser.parse_args()

  new_name = args.new_small_model_name
  if new_name is None:
    new_name = input(f'Enter new default model name (current: "{DEFAULT_MODEL}", leave empty to keep): ').strip()

  update_default_model_names(new_name or DEFAULT_MODEL)
  update_model_hash()
