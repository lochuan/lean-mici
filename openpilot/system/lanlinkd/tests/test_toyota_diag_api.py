import sys
import types

import pytest

from openpilot.system.lanlinkd import toyota_diag_api
from openpilot.system.lanlinkd.toyota.link import DiagLink
from openpilot.system.lanlinkd.toyota.session import SessionError

from .test_toyota_customization import ITEM
from .test_toyota_customization_flow import PROBE
from .toyota_fakes import FakeBodyEcu, FakeEcu, FakePanda, FakeUdsEcu


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


def make_active_api(tmp_path, ecus=None):
  ecu = FakeBodyEcu({0x12: bytes.fromhex("a020")}, {0x12: bytes.fromhex("ff0030")})
  panda = FakePanda(ecus if ecus is not None else {(0x750, 0x40): ecu})
  link = DiagLink(panda, elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  items = [ITEM, {**ITEM, "uid": 9, "readOnly": True}]
  return ecu, toyota_diag_api.ToyotaDiagApi(ActiveSession(link), str(tmp_path), customization_items=lambda: items)


class TestCustomizationEndpoints:
  def test_scan_one_ecu(self, tmp_path):
    body_ecu = FakeUdsEcu.from_probe(PROBE, "750", "40")
    _, api = make_active_api(tmp_path, ecus={(0x750, 0x40): body_ecu})
    status, body = api.customizations_scan({"ecu": "750/40"})
    assert status == 200
    assert body["ecu"] == "750/40" and body["status"] == "ok" and len(body["items"]) == 44
    assert {"key": "car_setting_warn_headlights_on_door_open", "value": 1, "raw": "01"} in body["items"]

  @pytest.mark.parametrize("request_body", [{}, {"ecu": "7FF"}, {"ecu": 7}])
  def test_scan_rejects_unknown_ecu(self, tmp_path, request_body):
    _, api = make_active_api(tmp_path)
    assert api.customizations_scan(request_body)[0] == 400

  def test_preview_then_write_then_restore(self, tmp_path):
    ecu, api = make_active_api(tmp_path)
    status, preview = api.customization_preview("1", {"option": 1})
    assert status == 200 and preview["before"] == "a020" and preview["after"] == "a000"
    status, result = api.customization_write("1", {"option": 1, "before": preview["before"]})
    assert status == 200 and result["verified"] and result["current"] == 1
    status, listing = api.backups_list()
    assert [b["id"] for b in listing["backups"]] == [result["backupId"]]
    assert listing["backups"][0]["localId"] == "12"
    status, restored = api.backup_restore(result["backupId"])
    assert status == 200 and restored["verified"]
    assert len(api.backups_list()[1]["backups"]) == 2   # 恢复前另存了一份
    assert ecu.memory[0x12] == bytes.fromhex("a020")

  def test_stale_before_is_409(self, tmp_path):
    _, api = make_active_api(tmp_path)
    status, body = api.customization_write("1", {"option": 1, "before": "0000"})
    assert status == 409 and "error" in body

  def test_read_only_item_is_409(self, tmp_path):
    _, api = make_active_api(tmp_path)
    assert api.customization_preview("9", {"option": 0})[0] == 409

  def test_bad_input(self, tmp_path):
    _, api = make_active_api(tmp_path)
    assert api.customization_preview("abc", {"option": 0})[0] == 404
    assert api.customization_preview("777", {"option": 0})[0] == 404
    assert api.customization_preview("1", {"option": 5})[0] == 400
    assert api.customization_preview("1", {})[0] == 400
    assert api.backup_restore("../../etc")[0] == 404

  def test_silent_ecu_is_504(self, tmp_path):
    _, api = make_active_api(tmp_path, ecus={})
    status, body = api.customization_preview("1", {"option": 1})
    assert status == 504 and "无响应" in body["error"]

  def test_malformed_response_is_502(self, tmp_path):
    garbled = FakeEcu({b"\x21\x12": b"\x61\x12" + bytes(10)}, misnumbered_frames=True)
    _, api = make_active_api(tmp_path, ecus={(0x750, 0x40): garbled})
    status, body = api.customization_preview("1", {"option": 1})
    assert status == 502 and "应答异常" in body["error"]

  def test_unexpected_error_is_json_500(self, tmp_path):
    class BrokenSession(FakeSession):
      def run(self, operation):
        raise RuntimeError("panda 断开")
    status, body = make_api(BrokenSession()).dtc_scan()
    assert status == 500 and "panda 断开" in body["error"]


# ---- 故障码 ----

DTC_TARGETS = [{"ecu": "7E0", "tx": 0x7E0, "sub": None, "name": "发动机"},
               {"ecu": "750/40", "tx": 0x750, "sub": 0x40, "name": "车身"}]


def make_dtc_api(tmp_path):
  engine = FakeEcu({b"\x3e": b"\x7e", b"\x13": bytes.fromhex("5301 0133"), b"\x04": b"\x44"})
  link = DiagLink(FakePanda({(0x7E0, None): engine}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return engine, toyota_diag_api.ToyotaDiagApi(ActiveSession(link), str(tmp_path), dtc_targets=lambda: DTC_TARGETS)


class TestDtcEndpoints:
  def test_scan_lists_responding_ecus(self, tmp_path):
    _, api = make_dtc_api(tmp_path)
    status, body = api.dtc_scan()
    assert status == 200
    assert [(e["ecu"], e["name"]) for e in body["ecus"]] == [("7E0", "发动机")]
    assert body["ecus"][0]["dtcs"][0]["code"] == "P0133"

  def test_clear_reports_each_ecu(self, tmp_path):
    engine, api = make_dtc_api(tmp_path)
    status, body = api.dtc_clear({"ecus": ["7E0", "750/40"]})
    assert status == 200
    assert body["results"][0] == {"ecu": "7E0", "ok": True}
    assert body["results"][1]["ecu"] == "750/40" and body["results"][1]["ok"] is False
    assert b"\x04" in engine.requests

  @pytest.mark.parametrize("body", [{}, {"ecus": []}, {"ecus": "7E0"}, {"ecus": [1]}, {"ecus": ["7FF"]}])
  def test_clear_rejects_bad_ecu_list(self, tmp_path, body):
    engine, api = make_dtc_api(tmp_path)
    assert api.dtc_clear(body)[0] == 400
    assert b"\x04" not in engine.requests


# ---- 实时数据 ----

def _pid(id_, **fields):
  base = {"id": id_, "name": {"en": f"pid{id_}"}, "hdr": "7E0", "sub": None, "cmd": "2104", "type": 1, "sbi": 0, "dl": 1,
          "bit": 0, "mul": 1.0, "div": 1.0, "ofs": 0.0, "signed": False, "formula": "", "unit": "km/h"}
  return {**base, **fields}


PID_PROFILES = [
  {"id": "new", "name": "2016+", "pids": [_pid(1), _pid(2, hdr="750", sub="40", cmd="221001"), _pid(3, type=3)]},
  {"id": "old", "name": "2009-2015", "pids": [_pid(5)]},
]


def make_pid_api(tmp_path, vin=""):
  engine = FakeEcu({bytes.fromhex("2104"): bytes.fromhex("6104 2a")})
  link = DiagLink(FakePanda({(0x7E0, None): engine}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return toyota_diag_api.ToyotaDiagApi(ActiveSession(link), str(tmp_path), pid_profiles=lambda: PID_PROFILES,
                                       vin=lambda: vin, default_profile_id=lambda v: "old" if v else "new")


class TestPidEndpoints:
  def test_catalog_groups_readable_pids_by_ecu(self, tmp_path):
    status, body = make_pid_api(tmp_path).pids_get()
    assert status == 200
    assert body["profile"] == {"id": "new", "name": "2016+"}
    assert body["profiles"] == [{"id": "new", "name": "2016+"}, {"id": "old", "name": "2009-2015"}]
    assert [g["ecu"].split()[0] for g in body["groups"]] == ["7E0", "750/40"]
    assert body["groups"][0]["pids"] == [{"id": 1, "name": "pid1", "unit": "km/h"}]   # 动作 PID 3 不出现

  def test_default_profile_follows_vin(self, tmp_path):
    assert make_pid_api(tmp_path, vin="JT...").pids_get()[1]["profile"]["id"] == "old"

  def test_select_profile(self, tmp_path):
    api = make_pid_api(tmp_path)
    assert api.pids_select_profile({"id": "old"}) == (204, None)
    assert api.pids_get()[1]["profile"]["id"] == "old"
    assert api.pids_select_profile({"id": "nope"})[0] == 400

  def test_read_values(self, tmp_path):
    status, body = make_pid_api(tmp_path).pids_read({"ids": [1]})
    assert status == 200
    assert body == {"values": [{"id": 1, "value": 42, "text": "42"}]}

  @pytest.mark.parametrize("body", [{}, {"ids": []}, {"ids": [True]}, {"ids": ["1"]}, {"ids": list(range(41))}])
  def test_read_rejects_bad_ids(self, tmp_path, body):
    assert make_pid_api(tmp_path).pids_read(body)[0] == 400

  def test_action_pid_cannot_be_read(self, tmp_path):
    _, body = make_pid_api(tmp_path).pids_read({"ids": [3]})
    assert body["values"][0]["value"] is None


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
