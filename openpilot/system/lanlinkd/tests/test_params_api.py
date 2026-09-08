import pytest

from openpilot.system.lanlinkd.params_api import (
  BLOCKED_PARAMS, VERSION_KEY, delete_param, list_params, read_param,
  type_name, validate_value, write_param)

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
