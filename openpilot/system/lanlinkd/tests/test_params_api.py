import json

import pytest

from openpilot.system.lanlinkd.params_api import (
  BLOCKED_PARAMS, VERSION_KEY, delete_param, list_params, read_all, read_param,
  type_name, to_str, validate_value, write_param)

BOOL, STRING, INT, FLOAT, JSON, BYTES = 1, 0, 2, 3, 5, 6  # common/params.h ParamKeyType


class FakeStore:
  """duck-type Params：all_keys/get/get_type/put/remove"""
  def __init__(self, initial=None, types=None):
    self.data = dict(initial or {})
    self.types = dict(types or {})
    self.versions = 0

  def all_keys(self):
    return list(self.data.keys())

  def get(self, key, block=False, return_default=False):
    return self.data.get(key)

  def get_bool(self, key, block=False):
    return self.data.get(key) == "1"

  def get_type(self, key):
    return self.types.get(key, STRING)

  def put(self, key, dat, block=False):
    self.data[key] = dat
    if key == VERSION_KEY:
      self.versions += 1

  def remove(self, key):
    self.data.pop(key, None)


@pytest.fixture
def store():
  return FakeStore(
    initial={"IsMetric": "1", "GithubSshKeys": "ssh-ed25519 AAAA", "LanLinkParamsVersion": "7"},
    types={"IsMetric": BOOL, "GithubSshKeys": STRING, "LanLinkParamsVersion": INT})


class TestTypeName:
  def test_names(self):
    assert type_name(BOOL) == "BOOL"
    assert type_name(STRING) == "STRING"
    assert type_name(INT) == "INT"

  def test_unknown_int(self):
    assert type_name(99) == "99"


class TestValidateValue:
  def test_bool_strict(self):
    assert validate_value("BOOL", "1") and validate_value("BOOL", "0")
    assert not validate_value("BOOL", "true") and not validate_value("BOOL", "")

  def test_numeric(self):
    assert validate_value("INT", "42") and not validate_value("INT", "4.5")
    assert validate_value("FLOAT", "0.5") and not validate_value("FLOAT", "abc")

  def test_json(self):
    assert validate_value("JSON", '{"a": 1}') and not validate_value("JSON", "{bad")

  def test_free_types(self):
    assert validate_value("STRING", "anything") and validate_value("BYTES", "\x00raw")


class TestRead:
  def test_read_ok(self, store):
    code, value = read_param(store, "IsMetric")
    assert (code, value) == (200, "1")

  def test_read_blocked_is_403(self, store):
    code, _ = read_param(store, "GithubSshKeys")
    assert code == 403

  def test_read_version_key_is_200(self, store):
    # spec §5.3：版本计数网页可读，用于感知车机端改动
    code, value = read_param(store, VERSION_KEY)
    assert (code, value) == (200, "7")

  def test_read_missing_is_404(self, store):
    assert read_param(store, "NoSuchKey")[0] == 404


class TestWrite:
  def test_write_ok_bumps_version(self, store):
    code, _ = write_param(store, "IsMetric", "0")
    assert code == 204
    assert store.data["IsMetric"] == "0"
    assert store.versions == 1

  def test_write_blocked_is_403(self, store):
    assert write_param(store, "GithubSshKeys", "evil")[0] == 403
    assert store.versions == 0

  def test_write_bad_type_is_400(self, store):
    assert write_param(store, "IsMetric", "true")[0] == 400

  def test_write_unknown_key_is_404(self, store):
    assert write_param(store, "NoSuchKey", "1")[0] == 404


class TestDelete:
  def test_delete_ok(self, store):
    assert delete_param(store, "IsMetric")[0] == 204
    assert "IsMetric" not in store.data

  def test_delete_blocked_is_403(self, store):
    assert delete_param(store, "GithubSshKeys")[0] == 403


class TestList:
  def test_list_shape(self, store):
    listing = list_params(store)
    assert listing["IsMetric"] == {"type": "BOOL", "blocked": False}
    assert listing["GithubSshKeys"] == {"type": "STRING", "blocked": True}
    assert listing["LanLinkParamsVersion"]["blocked"] is True


def test_blocked_params_contains_critical_keys():
  assert {"LanLinkPasswordHash", "LanLinkParamsVersion", "GithubSshKeys",
          "SshEnabled", "ParamsVersion", "OnroadCycleRequested",
          "DoReboot", "DoShutdown", "DoUninstall",
          "AccessToken", "SecOCKey", "AssistNowToken",
          "LanLinkEnabled"} <= BLOCKED_PARAMS


class BytesFakeStore:
  """mimics libparams_c: all_keys()/get() return bytes, put() requires bytes"""
  def __init__(self, initial=None, types=None):
    self.data = {k.encode(): v.encode() for k, v in (initial or {}).items()}
    self.types = {k.encode(): v for k, v in (types or {}).items()}
    self.versions = 0

  def all_keys(self):
    return list(self.data.keys())

  def get(self, key, block=False, return_default=False):
    if isinstance(key, str):
      key = key.encode()
    return self.data.get(key)

  def get_type(self, key):
    if isinstance(key, str):
      key = key.encode()
    return self.types.get(key, STRING)

  def put(self, key, dat, block=False):
    if isinstance(key, str):
      key = key.encode()
    assert isinstance(dat, (bytes, type(None))), "C store requires bytes"
    self.data[key] = dat
    if key == VERSION_KEY.encode():
      self.versions += 1

  def remove(self, key):
    if isinstance(key, str):
      key = key.encode()
    self.data.pop(key, None)


@pytest.fixture
def bytes_store():
  return BytesFakeStore(
    initial={"IsMetric": "1", "GithubSshKeys": "ssh-ed25519 AAAA", "LanLinkParamsVersion": "7"},
    types={"IsMetric": BOOL, "GithubSshKeys": STRING, "LanLinkParamsVersion": INT})


class TestBytesStore:
  """real AGNOS Params returns bytes; the API boundary must expose str"""

  def test_to_str(self):
    assert to_str(b"abc") == "abc"
    assert to_str("abc") == "abc"
    assert to_str(None) is None

  def test_list_keys_are_str_and_json_safe(self, bytes_store):
    listing = list_params(bytes_store)
    assert listing["IsMetric"] == {"type": "BOOL", "blocked": False}
    assert listing["GithubSshKeys"] == {"type": "STRING", "blocked": True}
    json.dumps(listing)

  def test_read_returns_str(self, bytes_store):
    assert read_param(bytes_store, "IsMetric") == (200, "1")

  def test_read_all_str_values_excludes_blocked(self, bytes_store):
    all_params = read_all(bytes_store)
    assert all_params["IsMetric"] == "1"
    assert "GithubSshKeys" not in all_params
    json.dumps(all_params)

  def test_write_finds_bytes_key_and_puts_bytes(self, bytes_store):
    assert write_param(bytes_store, "IsMetric", "0")[0] == 204
    assert bytes_store.data[b"IsMetric"] == b"0"
    assert write_param(bytes_store, "NoSuchKey", "1")[0] == 404

  def test_write_bumps_version_from_bytes(self, bytes_store):
    assert write_param(bytes_store, "IsMetric", "0")[0] == 204
    assert bytes_store.data[VERSION_KEY.encode()] == b"8"

  def test_delete_finds_bytes_key(self, bytes_store):
    assert delete_param(bytes_store, "IsMetric")[0] == 204
    assert b"IsMetric" not in bytes_store.data
