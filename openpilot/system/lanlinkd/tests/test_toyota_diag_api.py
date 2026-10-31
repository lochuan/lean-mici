import sys
import types

import pytest

from openpilot.system.lanlinkd import toyota_diag_api
from openpilot.system.lanlinkd.toyota.link import DiagLink
from openpilot.system.lanlinkd.toyota.session import SessionError

from .test_toyota_customization_flow import PROBE
from .toyota_fakes import FakeEcu, FakePanda, FakeUdsEcu


class FakeSession:
  def __init__(self, enter_error: SessionError | None = None):
    self.enter_error = enter_error
    self.exited = False

  def snapshot(self):
    return {"state": "idle", "idleTimeoutS": 900}

  def enter(self):
    if self.enter_error:
      raise self.enter_error

  def exit(self):
    self.exited = True


def make_api(session):
  return toyota_diag_api.ToyotaDiagApi(session, backup_dir="/nonexistent")


class TestSessionEndpoints:
  def test_get_returns_snapshot(self):
    assert make_api(FakeSession()).session_get() == (200, {"state": "idle", "idleTimeoutS": 900})

  def test_enter_success_returns_new_snapshot(self):
    assert make_api(FakeSession()).session_enter() == (200, {"state": "idle", "idleTimeoutS": 900})

  def test_enter_failure_maps_status_and_message(self):
    api = make_api(FakeSession(SessionError(409, "车辆行驶中")))
    assert api.session_enter() == (409, {"error": "车辆行驶中"})

  def test_exit_is_204(self):
    session = FakeSession()
    assert make_api(session).session_exit() == (204, None)
    assert session.exited


class TestCatalogEndpoint:
  def test_catalog_works_without_session_and_hides_dtc_text(self):
    status, body = make_api(FakeSession()).catalog_get()
    assert status == 200
    assert len(body["ecus"]) == 41 and len(body["settings"]) == 130 and len(body["categories"]) == 18
    assert {"live", "liveTools", "battery"} <= body.keys()
    assert "dtcText" not in body   # 故障码说明由后端拼好随扫描结果下发


class TestPandadRunning:
  def test_native_or_python_pandad_counts(self):
    assert toyota_diag_api.pandad_running(run=lambda cmd: cmd[0] == "pidof")
    assert toyota_diag_api.pandad_running(run=lambda cmd: cmd[0] == "pgrep")
    assert not toyota_diag_api.pandad_running(run=lambda cmd: False)


# ---- 定制 / 备份：真实流程 + 帧级假 ECU ----


class ActiveSession(FakeSession):
  def __init__(self, link):
    super().__init__()
    self.link = link

  def run(self, operation):
    return operation(self.link)


HEADLIGHTS = "car_setting_warn_headlights_on_door_open"   # 750/40 DID 2270，实车值 01（开启）


def make_active_api(tmp_path, ecus=None):
  body_ecu = FakeUdsEcu.from_probe(PROBE, "750", "40")
  panda = FakePanda(ecus if ecus is not None else {(0x750, 0x40): body_ecu})
  link = DiagLink(panda, elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return body_ecu, toyota_diag_api.ToyotaDiagApi(ActiveSession(link), str(tmp_path))


class TestCustomizationEndpoints:
  def test_scan_one_ecu(self, tmp_path):
    _, api = make_active_api(tmp_path)
    status, body = api.customizations_scan({"ecu": "750/40"})
    assert status == 200
    assert body["ecu"] == "750/40" and body["status"] == "ok" and len(body["items"]) == 44
    assert {"key": HEADLIGHTS, "value": 1, "raw": "01"} in body["items"]

  @pytest.mark.parametrize("request_body", [{}, {"ecu": "7FF"}, {"ecu": 7}])
  def test_scan_rejects_unknown_ecu(self, tmp_path, request_body):
    _, api = make_active_api(tmp_path)
    assert api.customizations_scan(request_body)[0] == 400

  def test_write_list_and_restore(self, tmp_path):
    body_ecu, api = make_active_api(tmp_path)
    status, result = api.customization_write(HEADLIGHTS, {"value": 0, "before": "01"})
    assert status == 200 and result["verified"] and result["value"] == 0
    assert body_ecu.memory[0x2270] == b"\x00"
    status, listing = api.backups_list()
    assert status == 200
    assert listing["backups"] == [{"id": result["backupId"], "time": listing["backups"][0]["time"], "key": HEADLIGHTS,
                                   "name": "开门时大灯未关提醒", "ecu": "车身舒适系统",
                                   "before": {"value": 1, "label": "开启"}, "after": {"value": 0, "label": "关闭"},
                                   "restored": False}]
    status, restored = api.backup_restore(result["backupId"])
    assert status == 200 and restored["verified"] and restored["value"] == 1
    assert body_ecu.memory[0x2270] == b"\x01"
    listing = api.backups_list()[1]["backups"]
    assert len(listing) == 2 and next(b for b in listing if b["id"] == result["backupId"])["restored"] is True

  def test_old_format_backups_are_not_listed(self, tmp_path):
    (tmp_path / "0123456789ab.json").write_text('{"id": "0123456789ab", "time": 1, "uid": 10001, "data": "a020"}')
    _, api = make_active_api(tmp_path)
    assert api.backups_list() == (200, {"backups": []})
    assert api.backup_restore("0123456789ab")[0] == 404

  def test_stale_before_is_409(self, tmp_path):
    _, api = make_active_api(tmp_path)
    status, body = api.customization_write(HEADLIGHTS, {"value": 0, "before": "00"})
    assert status == 409 and "重新扫描" in body["error"]

  @pytest.mark.parametrize("key, request_body, expected", [
    ("nope", {"value": 0, "before": "01"}, 404),
    (HEADLIGHTS, {"value": 5, "before": "01"}, 400),                      # 不是选项之一
    (HEADLIGHTS, {"value": True, "before": "01"}, 400),
    (HEADLIGHTS, {"value": 0}, 400),                                      # 缺 before
    ("car_setting_speaker_volume", {"value": 15, "before": "00"}, 400),   # 数值型超出 1～14
  ])
  def test_bad_write_input(self, tmp_path, key, request_body, expected):
    _, api = make_active_api(tmp_path)
    assert api.customization_write(key, request_body)[0] == expected

  def test_bad_backup_id_is_404(self, tmp_path):
    _, api = make_active_api(tmp_path)
    assert api.backup_restore("../../etc")[0] == 404

  def test_silent_ecu_is_504(self, tmp_path):
    _, api = make_active_api(tmp_path, ecus={})
    status, body = api.customization_write(HEADLIGHTS, {"value": 0, "before": "01"})
    assert status == 504 and "无响应" in body["error"]

  def test_malformed_response_is_502(self, tmp_path):
    garbled = FakeEcu({b"\x22\x22\x70": b"\x62\x22\x70" + bytes(10)}, misnumbered_frames=True)
    _, api = make_active_api(tmp_path, ecus={(0x750, 0x40): garbled})
    status, body = api.customization_write(HEADLIGHTS, {"value": 0, "before": "01"})
    assert status == 502 and "应答异常" in body["error"]

  def test_unexpected_error_is_json_500(self, tmp_path):
    class BrokenSession(FakeSession):
      def run(self, operation):
        raise RuntimeError("panda 断开")
    status, body = make_api(BrokenSession()).customizations_scan({"ecu": "750/40"})
    assert status == 500 and "panda 断开" in body["error"]


# ---- 故障码 ----

def make_dtc_api(tmp_path):
  body_ecu = FakeUdsEcu.from_probe(PROBE, "750", "40")
  body_ecu.responses[bytes.fromhex("14ffffff")] = b"\x54"
  _, api = make_active_api(tmp_path, ecus={(0x750, 0x40): body_ecu})
  return body_ecu, api


class TestDtcEndpoints:
  def test_scan_one_ecu_with_text(self, tmp_path):
    _, api = make_dtc_api(tmp_path)
    status, body = api.dtc_scan({"ecu": "750/40"})
    assert status == 200
    assert (body["ecu"], body["status"], body["protocol"]) == ("750/40", "ok", "uds")
    assert body["dtcs"][1] == {"code": "U0163-87", "status": 0x28, "text": "与导航控制模块通信丢失（报文丢失）"}

  @pytest.mark.parametrize("request_body", [{}, {"ecu": "7FF"}, {"ecu": 7}])
  def test_scan_rejects_unknown_ecu(self, tmp_path, request_body):
    _, api = make_dtc_api(tmp_path)
    assert api.dtc_scan(request_body)[0] == 400

  def test_clear_reports_each_ecu(self, tmp_path):
    body_ecu, api = make_dtc_api(tmp_path)
    status, body = api.dtc_clear({"ecus": ["750/40", "7C4"]})
    assert status == 200
    assert body["results"] == [{"ecu": "750/40", "ok": True}, {"ecu": "7C4", "ok": False, "error": "无响应"}]
    assert bytes.fromhex("14ffffff") in body_ecu.requests

  @pytest.mark.parametrize("request_body", [{}, {"ecus": []}, {"ecus": "750/40"}, {"ecus": [1]}, {"ecus": ["7FF"]}])
  def test_clear_rejects_bad_ecu_list(self, tmp_path, request_body):
    body_ecu, api = make_dtc_api(tmp_path)
    assert api.dtc_clear(request_body)[0] == 400
    assert bytes.fromhex("14ffffff") not in body_ecu.requests


# ---- 实时数据 ----

class TestLiveEndpoints:
  def test_read_values(self, tmp_path):
    engine = FakeUdsEcu.from_probe(PROBE, "700", None)
    _, api = make_active_api(tmp_path, ecus={(0x700, None): engine})
    status, body = api.live_read({"keys": ["car_tool_mileage", "car_tool_mileage_unit"]})
    assert status == 200
    assert body == {"values": [{"key": "car_tool_mileage", "value": 13875, "text": "13875"},
                               {"key": "car_tool_mileage_unit", "value": 1, "text": "公里"}]}

  @pytest.mark.parametrize("request_body", [{}, {"keys": []}, {"keys": "car_tool_mileage"}, {"keys": [1]},
                                            {"keys": ["nope"]}, {"keys": ["car_tool_mileage"] * 41}])
  def test_rejects_bad_keys(self, tmp_path, request_body):
    _, api = make_active_api(tmp_path)
    assert api.live_read(request_body)[0] == 400


class TestOpenPandaLink:
  def test_failed_setup_closes_panda(self, monkeypatch):
    class BrokenPanda:
      closed = False

      def set_safety_mode(self, mode, param=0):
        raise RuntimeError("usb gone")

      def close(self):
        BrokenPanda.closed = True

    monkeypatch.setitem(sys.modules, "panda", types.SimpleNamespace(Panda=BrokenPanda))
    with pytest.raises(RuntimeError):
      toyota_diag_api.open_panda_link()
    assert BrokenPanda.closed
