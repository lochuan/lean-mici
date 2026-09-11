"""vehicle_api 的纯逻辑测试。

不测 capnp 解码（需要真实 CarParamsPersistent，见设备端 verify 脚本），
只测状态判定 / 选择 / 清除 —— 也就是 UI 直接依赖的那部分。
"""
from openpilot.system.lanlinkd import vehicle_api

from .test_models_api import FakeParams

# car_list.json 的真实结构（取自设备上的 72 条里的两条）
CAR_LIST = {
  "Toyota Sienna 2021-23": {
    "platform": "TOYOTA_SIENNA_4TH_GEN", "make": "Toyota", "brand": "toyota",
    "model": "Sienna", "year": ["2021", "2022", "2023"], "package": "All",
  },
  "Lexus ES 2019-25": {
    "platform": "LEXUS_ES_TSS2", "make": "Lexus", "brand": "toyota",
    "model": "ES", "year": ["2019"], "package": "All",
  },
}


def state(params, car_list=CAR_LIST):
  return vehicle_api.vehicle_state(params, car_list=car_list)


class TestVehicleState:
  def test_no_car_at_all(self):
    st = state(FakeParams())
    assert st["source"] == "none"
    assert st["fingerprinted"] is False
    assert st["platform"] == ""
    assert st["name"] == ""
    # 没车也要能选：choices 必须照常返回，否则 Vehicle 页是个死页面
    assert st["choices"] == sorted(CAR_LIST)

  def test_manual_selection_wins_over_stale_autodetect(self):
    # 改完车型要下次上电才生效，期间 CarParamsPersistent 还是旧的自动识别结果。
    # 此时必须显示用户选的那个，否则用户会以为没保存上。
    params = FakeParams({
      vehicle_api.BUNDLE_KEY: {**CAR_LIST["Lexus ES 2019-25"], "name": "Lexus ES 2019-25"},
    })
    st = state(params)
    assert st["source"] == "manual"
    assert st["name"] == "Lexus ES 2019-25"
    assert st["platform"] == "LEXUS_ES_TSS2"
    assert st["brand"] == "toyota"
    assert st["fingerprinted"] is True

  def test_empty_bundle_is_not_a_manual_selection(self):
    # Params 里留下一个空 dict 不等于"用户选过车"
    assert state(FakeParams({vehicle_api.BUNDLE_KEY: {}}))["source"] == "none"

  def test_garbage_bundle_does_not_explode(self):
    # 类型不对就当没选过，Vehicle 页仍要能打开
    for junk in ("nonsense", 42, [], None):
      assert state(FakeParams({vehicle_api.BUNDLE_KEY: junk}))["source"] == "none"

  def test_undecodable_car_params_degrades_to_unfingerprinted(self):
    # 垃圾字节 → 解不出来 → "未识别"，而不是 500
    st = state(FakeParams({vehicle_api.CP_KEY: b"not a capnp message"}))
    assert st["source"] == "none"
    assert st["fingerprinted"] is False
    assert st["detected"] == {}


class TestSelectPlatform:
  def test_select_writes_the_shape_params_migration_expects(self):
    params = FakeParams()
    code, _ = vehicle_api.select_platform(params, "Toyota Sienna 2021-23", car_list=CAR_LIST)
    assert code == 204
    key, value = params.puts[-1]
    assert key == vehicle_api.BUNDLE_KEY
    # platform 是迁移逻辑唯一必需的字段；name 是 UI 显示用的
    assert value["platform"] == "TOYOTA_SIENNA_4TH_GEN"
    assert value["name"] == "Toyota Sienna 2021-23"
    assert value["brand"] == "toyota"

  def test_selection_round_trips_into_state(self):
    params = FakeParams()
    vehicle_api.select_platform(params, "Lexus ES 2019-25", car_list=CAR_LIST)
    assert state(params)["name"] == "Lexus ES 2019-25"

  def test_unknown_name_is_rejected_without_writing(self):
    params = FakeParams()
    code, msg = vehicle_api.select_platform(params, "Ford Model T", car_list=CAR_LIST)
    assert code == 404
    assert msg
    assert params.puts == []

  def test_empty_name_clears_back_to_autodetect(self):
    params = FakeParams({vehicle_api.BUNDLE_KEY: {"platform": "X", "name": "X"}})
    code, _ = vehicle_api.select_platform(params, "", car_list=CAR_LIST)
    assert code == 204
    assert vehicle_api.BUNDLE_KEY in params.removes
    assert state(params)["source"] == "none"


class TestCarList:
  def test_missing_file_returns_empty_not_raise(self):
    # 开发机上可能没有这个文件；不能让 Vehicle 页 500
    assert vehicle_api.load_car_list("/nonexistent/car_list.json") == {}

  def test_real_device_car_list_is_loadable(self):
    # 仓库里就有这个文件，路径拼错会在这里暴露
    cars = vehicle_api.load_car_list()
    assert len(cars) > 0
    assert all("platform" in v for v in cars.values())
