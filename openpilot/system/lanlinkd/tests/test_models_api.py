import json
import os

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
  return {"ref": ref, "display_name": name, "short_name": name.lower().replace(" ", "_"),
          "index": index, "generation": gen, "environment": "release",
          "runner": "snpe", "is_20hz": False, "minimum_selector_version": 19,
          "overrides": ([{"key": "folder", "value": folder}] if folder else [])}


def cache_param(*bundles):
  return {"bundles": list(bundles)}


def active_bundle(ref="ref-a", name="Model A"):
  return {"ref": ref, "displayName": name, "internalName": name.lower().replace(" ", "_"),
          "runner": "snpe", "minimumSelectorVersion": 19}


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
    raw["bundles"][3]["minimum_selector_version"] = 18  # version 不匹配 → 过滤
    p = FakeParams(data={"ModelManager_ModelsCache": raw})
    st = models_api.models_state(p, None, "/nonexistent")
    assert [b["ref"] for b in st["bundles"]] == ["ref-b", "ref-a", "ref-c"]
    b0 = st["bundles"][0]
    assert b0["displayName"] == "Model B" and b0["folder"] == "2026 World" and b0["fav"] is False
    assert b0["index"] == 2 and b0["runner"] == "snpe"

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
