from openpilot.system.lanlinkd import toyota_diag_api
from openpilot.system.lanlinkd.toyota.link import DiagLink
from openpilot.system.lanlinkd.toyota.session import SessionError

from .test_toyota_customization import ITEM
from .toyota_fakes import FakeBodyEcu, FakePanda


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
  def test_scan_shapes_items_for_the_web(self, tmp_path):
    _, api = make_active_api(tmp_path)
    status, body = api.customizations_scan()
    assert status == 200
    item = body["items"][0]
    assert item["uid"] == 1 and item["name"] == "提示音" and item["current"] == 0
    assert item["options"] == [{"index": 0, "title": "开"}, {"index": 1, "title": "关"}]
    assert item["ecu"].startswith("750/40")
    assert [i["writable"] for i in body["items"]] == [True, False]

  def test_preview_then_write_then_restore(self, tmp_path):
    ecu, api = make_active_api(tmp_path)
    status, preview = api.customization_preview("1", {"option": 1})
    assert status == 200 and preview["before"] == "a020" and preview["after"] == "a000"
    status, result = api.customization_write("1", {"option": 1, "before": preview["before"]})
    assert status == 200 and result["verified"] and result["current"] == 1
    status, listing = api.backups_list()
    assert [b["id"] for b in listing["backups"]] == [result["backupId"]]
    assert listing["backups"][0]["localId"] == "12"
    assert api.backup_restore(result["backupId"]) == (200, {"verified": True})
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
