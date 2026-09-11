import json

import pytest

from openpilot.system.lanlinkd.params_api import (
  BLOCKED_PARAMS, VERSION_KEY, coerce_value, delete_param, list_params, read_all, read_param,
  type_name, to_str, write_param)

BOOL, STRING, INT, FLOAT, JSON, BYTES = 1, 0, 2, 3, 5, 6  # common/params.h ParamKeyType


class FakeStore:
  """faithful duck-type of AGNOS Params（common/params.py）：
  - all_keys() 返回 bytes key（唯一 bytes 边界）
  - get() 返回 python 类型值（STRING->str, BOOL->bool, INT->int, ...）
  - put() 校验 value 的 python 类型匹配参数类型（python2cpp 门，不匹配抛 TypeError）
  """
  def __init__(self, initial=None, types=None):
    self.data = {k.encode(): v for k, v in (initial or {}).items()}
    self.types = {k.encode(): t for k, t in (types or {}).items()}
    # 静态 key 表：VERSION_KEY 恒注册为 INT（params_keys.h），即使无值
    self.types.setdefault(VERSION_KEY.encode(), INT)
    self.versions = 0

  def all_keys(self):
    # 真 Params.all_keys() 返回的是 params_keys.h 的**静态注册表**，与有没有
    # 设过值无关（设备实测：IsMetric 在 all_keys 里，但 get() 是 None）。
    # 所以这里要把"已注册但未赋值"的 key 也算进来，否则测不出未设置态的行为。
    return list({**{k: None for k in self.types}, **self.data}.keys())

  def get(self, key, block=False, return_default=False):
    k = key.encode() if isinstance(key, str) else key
    return self.data.get(k)

  def get_bool(self, key, block=False):
    v = self.get(key)
    return bool(v) if isinstance(v, bool) else v == "1"

  def get_type(self, key):
    k = key.encode() if isinstance(key, str) else key
    return self.types.get(k, STRING)

  def put(self, key, dat, block=False):
    k = key.encode() if isinstance(key, str) else key
    # 模拟 python2cpp：类型不匹配抛 TypeError（真 C store 的行为，500 的根因）
    expected = {STRING: str, BOOL: bool, INT: int, FLOAT: float, JSON: (dict, list), BYTES: bytes}
    t = self.get_type(key)
    if t in expected and not isinstance(dat, expected[t]):
      raise TypeError(f"Type mismatch while writing param {key}")
    self.data[k] = dat
    if k == VERSION_KEY.encode():
      self.versions += 1

  def remove(self, key):
    k = key.encode() if isinstance(key, str) else key
    self.data.pop(k, None)


@pytest.fixture
def store():
  return FakeStore(
    initial={"IsMetric": True, "GithubSshKeys": "ssh-ed25519 AAAA", "LanLinkParamsVersion": 7},
    types={"IsMetric": BOOL, "GithubSshKeys": STRING, "LanLinkParamsVersion": INT})


class TestTypeName:
  def test_names(self):
    assert type_name(BOOL) == "BOOL"
    assert type_name(STRING) == "STRING"
    assert type_name(INT) == "INT"

  def test_unknown_int(self):
    assert type_name(99) == "99"


class TestToStr:
  def test_bytes_and_str_and_none(self):
    assert to_str(b"abc") == "abc"
    assert to_str("abc") == "abc"
    assert to_str(None) is None
    assert to_str(7) == "7"


class TestCoerceValue:
  def test_bool(self):
    assert coerce_value("BOOL", "1") is True
    assert coerce_value("BOOL", "0") is False
    assert coerce_value("BOOL", "true") is True
    assert coerce_value("BOOL", "off") is False
    assert coerce_value("BOOL", "maybe") is None

  def test_numeric(self):
    assert coerce_value("INT", "42") == 42
    assert coerce_value("INT", "4.5") is None
    assert coerce_value("FLOAT", "0.5") == 0.5
    assert coerce_value("FLOAT", "abc") is None

  def test_json(self):
    assert coerce_value("JSON", '{"a": 1}') == {"a": 1}
    assert coerce_value("JSON", "{bad") is None

  def test_free_types(self):
    assert coerce_value("STRING", "anything") == "anything"
    assert coerce_value("BYTES", "raw") == b"raw"


class TestRead:
  def test_read_bool_canonical(self, store):
    # BOOL 类型值 True -> API 字符串 "1"（UI 可回写）
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
    assert store.data[b"IsMetric"] is False  # 写入的是 python bool（C store 要求）
    assert store.versions == 1

  def test_write_string_typed(self):
    s = FakeStore(initial={"SomeString": "a"}, types={"SomeString": STRING})
    code, _ = write_param(s, "SomeString", "b")
    assert code == 204
    assert s.data[b"SomeString"] == "b"  # STRING 写 str

  def test_write_blocked_is_403(self, store):
    assert write_param(store, "GithubSshKeys", "evil")[0] == 403
    assert store.versions == 0

  def test_write_bad_type_is_400(self, store):
    assert write_param(store, "IsMetric", "maybe")[0] == 400

  def test_write_unknown_key_is_404(self, store):
    assert write_param(store, "NoSuchKey", "1")[0] == 404


class TestDelete:
  def test_delete_ok(self, store):
    assert delete_param(store, "IsMetric")[0] == 204
    assert b"IsMetric" not in store.data

  def test_delete_blocked_is_403(self, store):
    assert delete_param(store, "GithubSshKeys")[0] == 403


class TestList:
  def test_list_shape(self, store):
    listing = list_params(store)
    assert listing["IsMetric"] == {"type": "BOOL", "blocked": False}
    assert listing["GithubSshKeys"] == {"type": "STRING", "blocked": True}
    assert listing["LanLinkParamsVersion"]["blocked"] is True

  def test_list_json_safe(self, store):
    json.dumps(list_params(store))


def test_blocked_params_contains_critical_keys():
  assert {"LanLinkPasswordHash", "LanLinkParamsVersion", "GithubSshKeys",
          "SshEnabled", "ParamsVersion", "OnroadCycleRequested",
          "DoReboot", "DoShutdown", "DoUninstall",
          "AccessToken", "SecOCKey", "AssistNowToken",
          "LanLinkEnabled"} <= BLOCKED_PARAMS


class TestReadAll:
  def test_str_values_excludes_blocked(self, store):
    all_params = read_all(store)
    assert all_params["IsMetric"] == "1"
    # VERSION_KEY 在黑名单里：read_all 不含它（UI 走 read_param 单独读，spec §5.3）
    assert "LanLinkParamsVersion" not in all_params
    assert "GithubSshKeys" not in all_params
    json.dumps(all_params)

  def test_unset_bool_reports_zero_not_absent(self):
    """已注册但未赋值的 BOOL 要报 "0"，不能省略。

    设备实测：80 个 schema key 里有 17 个处于这种状态（IsMetric、
    ExperimentalMode、EnforceTorqueControl…）。省略的话前端拿不到值，
    "关闭"和"该 param 不存在"就分不开了，而后者会被渲染成禁用态。
    device 侧到处用 params.get_bool()，它把未设置当 False，所以 "0"
    才是真实语义。
    """
    s = FakeStore(initial={}, types={"ExperimentalMode": BOOL, "IsMetric": BOOL})
    all_params = read_all(s)
    assert all_params["ExperimentalMode"] == "0"
    assert all_params["IsMetric"] == "0"

  def test_unset_non_bool_stays_absent(self):
    # 非 BOOL 没有可推导的默认值，猜一个反而会误导 UI
    s = FakeStore(initial={}, types={"SomeText": STRING, "SomeNum": INT})
    all_params = read_all(s)
    assert "SomeText" not in all_params
    assert "SomeNum" not in all_params


class TestReadUnsetParam:
  def test_unset_bool_reads_as_zero(self):
    s = FakeStore(initial={}, types={"ExperimentalMode": BOOL})
    assert read_param(s, "ExperimentalMode") == (200, "0")

  def test_unset_non_bool_is_404(self):
    s = FakeStore(initial={}, types={"SomeText": STRING})
    code, _ = read_param(s, "SomeText")
    assert code == 404

  def test_unregistered_key_is_still_404(self):
    s = FakeStore(initial={}, types={"ExperimentalMode": BOOL})
    code, _ = read_param(s, "NotAParam")
    assert code == 404


class TestTypedRoundsTrip:
  def test_int_write_read(self):
    s = FakeStore(initial={"LanLinkParamsVersion": 0}, types={"LanLinkParamsVersion": INT})
    assert write_param(s, "LanLinkParamsVersion", "0")[0] == 403  # 黑名单

  def test_string_roundtrip(self):
    s = FakeStore(initial={"SomeString": "a"}, types={"SomeString": STRING})
    code, _ = write_param(s, "SomeString", "b")
    assert code == 204
    assert s.data[b"SomeString"] == "b"
    assert read_param(s, "SomeString") == (200, "b")
