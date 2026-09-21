import json
import os
from unittest import mock

from openpilot.system.lanlinkd import models_api


class FakeParams:
  """记录写操作的 duck-type Params。"""

  def __init__(self, data=None):
    self.data = dict(data or {})
    self.puts = []
    self.removes = []

  def get(self, key):
    return self.data.get(key)

  def get_bool(self, key):
    v = self.data.get(key)
    return v in (True, "1", 1)

  def put(self, key, value, block=False):
    self.puts.append((key, value))
    self.data[key] = value

  def put_bool(self, key, value, block=False):
    self.puts.append((key, value))
    self.data[key] = value

  def remove(self, key):
    self.removes.append(key)
    self.data.pop(key, None)


def bundle(ref, name, folder="", index=0, gen=1):
  # overrides 用**字典**形态，与设备上 ModelManager_ModelsCache 的真实结构一致
  # （实测 {"folder": "Legacy Models", "lat": ".0", "long": ".3"}）。
  # 之前这里写成 [{key,value}] 列表，把 _folder 的解析 bug 给掩盖住了。
  return {"ref": ref, "display_name": name, "short_name": name.lower().replace(" ", "_"),
          "index": index, "generation": gen, "environment": "release",
          "runner": "snpe", "is_20hz": False, "minimum_selector_version": 29,
          "overrides": ({"folder": folder, "lat": ".0", "long": ".3"} if folder else {})}


def bundle_capnp_overrides(ref, name, folder, index=0):
  """overrides 的另一种形态：capnp 的 bundle.overrides 是 [{key,value}] 列表。"""
  b = bundle(ref, name, index=index)
  b["overrides"] = [{"key": "folder", "value": folder}]
  return b


def cache_param(*bundles):
  return {"bundles": list(bundles)}


def active_bundle(ref="ref-a", name="Model A"):
  return {"ref": ref, "displayName": name, "internalName": name.lower().replace(" ", "_"),
          "runner": "snpe", "minimumSelectorVersion": 29}


class TestModelsState:
  def test_empty_cache(self):
    p = FakeParams()
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["bundles"] == []
    assert st["active"] is None
    assert st["default_model"] == "CD210"
    assert st["download"] is None
    assert st["cache_size_mb"] == 0.0

  def test_bundles_mapped_and_sorted(self):
    raw = cache_param(
      bundle("ref-b", "Model B", folder="2026 World", index=2),
      bundle("ref-a", "Model A", folder="2026 World", index=1),
      bundle("ref-c", "Model C", folder="", index=0),
      bundle("ref-old", "Old", index=9))
    raw["bundles"][3]["minimum_selector_version"] = 28  # version 不匹配 → 过滤
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert [b["ref"] for b in st["bundles"]] == ["ref-b", "ref-a", "ref-c"]
    b0 = st["bundles"][0]
    assert b0["displayName"] == "Model B" and b0["folder"] == "2026 World" and b0["fav"] is False
    assert b0["index"] == 2 and b0["runner"] == "snpe"

  def test_folder_parsed_from_dict_overrides(self):
    """overrides 是字典时也要取到分组名。

    设备实测就是这种形态。解析错的后果不是少个字段：分组名全空，
    77 个模型会挤进一个没有名字的组，等于没有分组。
    """
    raw = cache_param(bundle("r1", "M1", folder="Legacy Models", index=1))
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["bundles"][0]["folder"] == "Legacy Models"

  def test_folder_parsed_from_capnp_list_overrides(self):
    # capnp 侧的 bundle.overrides 是 [{key,value}]，两种都要兼容
    raw = cache_param(bundle_capnp_overrides("r1", "M1", "2026 World Models", index=1))
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["bundles"][0]["folder"] == "2026 World Models"

  def test_missing_overrides_yields_empty_folder(self):
    raw = cache_param(bundle("r1", "M1", index=1))
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["bundles"][0]["folder"] == ""

  def test_string_version_fields_from_manifest(self):
    # 设备实测：manifest 的 minimum_selector_version/generation 是字符串
    raw = cache_param(bundle("ref-a", "Model A"))
    raw["bundles"][0]["minimum_selector_version"] = "29"
    raw["bundles"][0]["generation"] = "4"
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert [b["ref"] for b in st["bundles"]] == ["ref-a"]
    assert st["bundles"][0]["generation"] == 4

  def test_bad_version_dropped(self):
    raw = cache_param(bundle("ref-bad", "Bad"))
    raw["bundles"][0]["minimum_selector_version"] = "abc"
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["bundles"] == []

  def test_active_and_favs(self):
    p = FakeParams(data={
      "ModelManager_ModelsCache": cache_param(bundle("ref-a", "Model A"), bundle("ref-b", "Model B")),
      "ModelManager_ActiveBundle": active_bundle(),
      "ModelManager_Favs": "ref-b;ref-x"})
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["active"] == {"ref": "ref-a", "displayName": "Model A",
                            "internalName": "model_a", "runner": "snpe"}
    by = {b["ref"]: b for b in st["bundles"]}
    assert by["ref-b"]["fav"] is True and by["ref-a"]["fav"] is False
    assert st["favs"] == ["ref-b", "ref-x"]

  def test_download_merged(self):
    p = FakeParams(data={"ModelManager_ModelsCache": cache_param(bundle("ref-a", "A"))})
    dl = {"ref": "ref-b", "displayName": "Model B", "status": "downloading",
          "verifying": False, "progress": 42.5, "eta": 30}
    st = models_api.models_state(p, dl, "/nonexistent")
    assert st["download"]["progress"] == 42.5

  def test_cache_size(self, tmp_path):
    (tmp_path / "m.onnx").write_bytes(b"x" * 2048)
    (tmp_path / "sub").mkdir()
    p = FakeParams()
    st = models_api.models_state(p, None, str(tmp_path))
    assert st["cache_size_mb"] == round(2048 / 1024 / 1024, 1)


class TestSelect:
  def test_valid_ref_writes_download_ref(self):
    p = FakeParams(data={"ModelManager_ModelsCache": cache_param(bundle("ref-a", "A"))})
    code, _ = models_api.select(p, "ref-a")
    assert code == 204
    assert ("ModelManager_DownloadRef", "ref-a") in p.puts
    assert p.data.get("LanLinkParamsVersion", 0) >= 1

  def test_unknown_ref_404(self):
    p = FakeParams()
    code, _ = models_api.select(p, "nope")
    assert code == 404
    assert p.puts == []

  def test_default_removes_active_bundle(self):
    p = FakeParams(data={"ModelManager_ActiveBundle": active_bundle()})
    code, _ = models_api.select(p, "Default")
    assert code == 204
    assert p.removes == ["ModelManager_ActiveBundle"]
    assert all(k != "ModelManager_DownloadRef" for k, _ in p.puts)


class TestPinGate:
  """目录 tinygrad_ref vs 本机树的门控：三态 + select 拒绝。"""

  CATALOG = "e837e367aac9e1a66e689f4f32ce20ca9367df13"
  DEVICE = "9cd40014f651ac2472b3b5fc3b12178ab4bb1c66"

  def test_mismatch_marks_bundles_and_detail(self):
    raw = cache_param(bundle("ref-a", "Model A"), bundle("ref-b", "Model B"))
    raw["tinygrad_ref"] = self.CATALOG
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    with mock.patch.object(models_api, "_device_tinygrad_ref", return_value=self.DEVICE):
      st = models_api.models_state(p, None, "/nonexistent")
    assert st["pin_mismatch_detail"] and "e837e367" in st["pin_mismatch_detail"] and "9cd40014" in st["pin_mismatch_detail"]
    assert all(b["pinCompatible"] is False for b in st["bundles"])

  def test_match_is_compatible_and_no_detail(self):
    raw = cache_param(bundle("ref-a", "Model A"))
    raw["tinygrad_ref"] = self.DEVICE
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    with mock.patch.object(models_api, "_device_tinygrad_ref", return_value=self.DEVICE):
      st = models_api.models_state(p, None, "/nonexistent")
    assert st["pin_mismatch_detail"] is None
    assert all(b["pinCompatible"] is True for b in st["bundles"])

  def test_unknown_pin_passes_through(self):
    # 旧 manifest 无 tinygrad_ref（或设备 pin 取不到）：旧行为放行，不标不拦
    raw = cache_param(bundle("ref-a", "Model A"))
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert st["pin_mismatch_detail"] is None
    assert all(b["pinCompatible"] is None for b in st["bundles"])

  def test_select_refuses_incompatible_with_reason(self):
    raw = cache_param(bundle("ref-a", "Model A"))
    raw["tinygrad_ref"] = self.CATALOG
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    with mock.patch.object(models_api, "_device_tinygrad_ref", return_value=self.DEVICE):
      code, msg = models_api.select(p, "ref-a")
    assert code == 409
    assert "e837e367" in msg and "9cd40014" in msg and "不兼容" in msg
    assert all(k != "ModelManager_DownloadRef" for k, _ in p.puts)  # 不入队

  def test_select_unknown_pin_queues_normally(self):
    p = FakeParams(data={"ModelManager_ModelsCache": cache_param(bundle("ref-a", "A"))})
    code, _ = models_api.select(p, "ref-a")
    assert code == 204
    assert ("ModelManager_DownloadRef", "ref-a") in p.puts


class TestActions:
  def test_cancel(self):
    p = FakeParams(data={"ModelManager_DownloadRef": "ref-a"})
    code, _ = models_api.cancel(p)
    assert code == 204 and p.removes == ["ModelManager_DownloadRef"]

  def test_refresh_writes_zero(self):
    p = FakeParams()
    code, _ = models_api.refresh(p)
    assert code == 204
    assert ("ModelManager_LastSyncTime", 0) in p.puts

  def test_clear_cache(self):
    p = FakeParams()
    code, _ = models_api.clear_cache(p)
    assert code == 204
    assert ("ModelManager_ClearCache", True) in p.puts

  def test_fav_on_off(self):
    p = FakeParams()
    assert models_api.set_fav(p, "ref-a", True)[0] == 204
    assert p.data["ModelManager_Favs"] == "ref-a"
    assert models_api.set_fav(p, "ref-b", True)[0] == 204
    assert p.data["ModelManager_Favs"] == "ref-a;ref-b"
    assert models_api.set_fav(p, "ref-a", False)[0] == 204
    assert p.data["ModelManager_Favs"] == "ref-b"

  def test_fav_empty_ref_400(self):
    p = FakeParams()
    assert models_api.set_fav(p, "", True)[0] == 400
