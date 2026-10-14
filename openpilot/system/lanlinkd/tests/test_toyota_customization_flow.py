import os

import pytest

from openpilot.system.lanlinkd.toyota import catalog
from openpilot.system.lanlinkd.toyota import customization as cz
from openpilot.system.lanlinkd.toyota.backups import BackupStore
from openpilot.system.lanlinkd.toyota.link import DiagLink

from .toyota_fakes import FakeBodyEcu, FakeEcu, FakePanda, FakeUdsEcu
from .test_toyota_customization import ITEM

OTHER_ECU_ITEM = {**ITEM, "uid": 2, "tx": "7C4", "sub": None, "readId": "81", "writeId": "81"}


def make(memory=None, reject_writes=None, tmp_path=None):
  ecu = FakeBodyEcu(memory or {0x12: bytes.fromhex("a020")}, {0x12: bytes.fromhex("ff0030")}, reject_writes)
  link = DiagLink(FakePanda({(0x750, 0x40): ecu}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return ecu, link, BackupStore(str(tmp_path)) if tmp_path else None


class TestScan:
  def test_lists_supported_items_with_current_value(self):
    _, link, _ = make()
    result = cz.scan(link, [ITEM, OTHER_ECU_ITEM])
    items = result["items"]
    # 0x7C4 不在线 → 该 ECU 的项不出现
    assert [i["uid"] for i in items] == [1]
    assert items[0]["current"] == 0
    assert items[0]["raw"] == "a020"

  def test_report_explains_each_ecu(self):
    # 列表为空时用户要能看出卡在哪一步：ECU 不在线 / A803 被拒 / 掩码不含该项 / 读值失败
    rejecting = FakeEcu({b"\x3e": b"\x7e", b"\xa8\x03": b"\x7f\xa8\x22"})
    quiet_after_probe = FakeEcu({b"\x3e": b"\x7e"})
    unreadable = FakeBodyEcu({}, {0x12: bytes.fromhex("ff0030")})
    link = DiagLink(FakePanda({(0x750, 0x40): rejecting, (0x7C4, None): quiet_after_probe, (0x7C0, None): unreadable}),
                    elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    third = {**ITEM, "uid": 3, "tx": "7C0", "sub": None}
    result = cz.scan(link, [ITEM, OTHER_ECU_ITEM, third, {**third, "uid": 4}])
    assert result["items"] == []
    assert result["ecus"] == [
      {"tx": "750", "sub": "40", "total": 1, "supported": 0, "read": 0, "status": "rejected", "nrc": "22"},
      {"tx": "7C4", "sub": None, "total": 1, "supported": 0, "read": 0, "status": "silent"},
      {"tx": "7C0", "sub": None, "total": 2, "supported": 2, "read": 0, "status": "ok", "masks": 1, "readError": "NRC 11"},
    ]

  def test_malformed_ecu_is_reported_and_scan_continues(self):
    # 实车：750/AD 天窗的 A8 03 应答序号错乱曾让整个扫描变成 HTTP 500
    sunroof = FakeEcu({b"\x3e": b"\x7e", b"\xa8\x03": b"\xe8\x03" + bytes(range(20))}, misnumbered_frames=True)
    body = FakeBodyEcu({0x12: bytes.fromhex("a020")}, {0x12: bytes.fromhex("ff0030")})
    link = DiagLink(FakePanda({(0x750, 0xAD): sunroof, (0x750, 0x40): body}),
                    elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan(link, [{**ITEM, "uid": 5, "sub": "AD"}, ITEM])
    assert [i["uid"] for i in result["items"]] == [1]
    report = result["ecus"][0]
    assert (report["sub"], report["status"]) == ("AD", "malformed")
    assert "invalid consecutive frame index" in report["malformedReason"]

  def test_report_marks_offline_ecu(self):
    _, link, _ = make()
    report = cz.scan(link, [OTHER_ECU_ITEM])["ecus"]
    assert report == [{"tx": "7C4", "sub": None, "total": 1, "supported": 0, "read": 0, "status": "offline"}]


class TestWrite:
  def test_preview_shows_compressed_before_and_after(self):
    _, link, _ = make()
    assert cz.preview(link, ITEM, 1) == {"before": "a020", "after": "a000", "payload": "3b12a00000"}

  def test_write_backs_up_writes_and_verifies(self, tmp_path):
    ecu, link, store = make(tmp_path=tmp_path)
    result = cz.write(link, ITEM, 1, "a020", store)
    assert ecu.memory[0x12] == bytes.fromhex("a000")
    assert result["verified"] and result["current"] == 1
    backup = store.get(result["backupId"])
    assert backup["data"] == "a020" and backup["uid"] == 1 and backup["mask"] == "ff0030"

  def test_write_refuses_when_ecu_changed_since_preview(self, tmp_path):
    ecu, link, store = make(tmp_path=tmp_path)
    with pytest.raises(cz.StaleDataError):
      cz.write(link, ITEM, 1, "ffff", store)
    assert ecu.memory[0x12] == bytes.fromhex("a020")
    assert store.list() == []

  def test_rejected_write_raises_with_nrc(self, tmp_path):
    _, link, store = make(reject_writes=b"\x7f\x3b\x22", tmp_path=tmp_path)
    with pytest.raises(cz.EcuRejectedError) as err:
      cz.write(link, ITEM, 1, "a020", store)
    assert err.value.nrc == 0x22

  def test_restore_backs_up_current_then_writes_backup_back(self, tmp_path):
    ecu, link, store = make(tmp_path=tmp_path)
    backup_id = cz.write(link, ITEM, 1, "a020", store)["backupId"]
    result = cz.restore(link, store.get(backup_id), store)
    assert result["verified"]
    assert ecu.memory[0x12] == bytes.fromhex("a020")
    # 恢复也是一次写入：恢复前的数据（a000）同样留了备份，可以再恢复回去
    assert store.get(result["backupId"])["data"] == "a000"

  def test_failed_backup_blocks_write(self, tmp_path):
    ecu, link, _ = make()
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    with pytest.raises(cz.BackupError):
      cz.write(link, ITEM, 1, "a020", BackupStore(str(blocker)))
    assert ecu.memory[0x12] == bytes.fromhex("a020")


# ---- Sienna 4 代：按 ECU 扫描（目录 + Carista 规则）----

PROBE = os.path.join(os.path.dirname(__file__), "fixtures", "sienna_ready_probe.jsonl")
# 目录里每个 UDS ECU 的定制项数 = 实车报告支持的项数（剔除脚踢感应后）
UDS_SETTING_COUNTS = {"750/40": 44, "750/67": 17, "750/B5": 14, "750/B8": 11, "7C4": 9, "750/A7": 7, "750/A4": 7,
                      "7C0": 3, "750/41": 2, "750/C7": 2}
WINDOW_SETTING = {"key": "car_setting_one_touch_test", "ecu": "750/90", "did": None, "localId": "11", "byteIndex": 2,
                  "mask": "20", "requiresBitSupportCheck": False}


def _replay_link(ecu_id: str):
  ecu = catalog.ecu(ecu_id)
  fake = FakeUdsEcu.from_probe(PROBE, ecu["tx"], ecu["sub"])
  address = (int(ecu["tx"], 16), int(ecu["sub"], 16) if ecu["sub"] else None)
  return DiagLink(FakePanda({address: fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)


def _settings_of(ecu_id: str) -> list[dict]:
  return [s for s in catalog.load_sienna()["settings"] if s["ecu"] == ecu_id]


class TestScanEcu:
  @pytest.mark.parametrize("ecu_id", sorted(UDS_SETTING_COUNTS))
  def test_replayed_car_reports_every_catalog_setting(self, ecu_id):
    result = cz.scan_ecu(_replay_link(ecu_id), catalog.ecu(ecu_id), _settings_of(ecu_id))
    assert result["status"] == "ok"
    assert len(result["items"]) == result["supported"] == result["read"] == UDS_SETTING_COUNTS[ecu_id]

  @pytest.mark.parametrize("ecu_id, key, value, raw", [
    ("750/40", "car_setting_warn_headlights_on_door_open", 1, "01"),   # 实车 62 2270 01
    ("750/B5", "car_setting_warn_on_key_in_car", 1, None),             # 要过前一字节的支持位检查
    ("750/41", "car_setting_rcta", 0, None),
  ])
  def test_values_match_the_probe(self, ecu_id, key, value, raw):
    items = {i["key"]: i for i in cz.scan_ecu(_replay_link(ecu_id), catalog.ecu(ecu_id), _settings_of(ecu_id))["items"]}
    assert items[key]["value"] == value
    if raw is not None:
      assert items[key]["raw"] == raw

  def test_bit_support_check_hides_setting_whose_support_bit_is_clear(self):
    setting = {**catalog.setting("car_setting_warn_on_key_in_car")}
    bitmap = bytearray(32)
    bitmap[(0x0C - 1) >> 3] = 1 << ((-0x0C) & 7)                      # DID 200C 在位图里
    fake = FakeUdsEcu({0x2000: bytes(bitmap), 0x200C: bytes.fromhex("0020")}, {b"\x3e": b"\x7e"})   # 前一字节 0x00：支持位没置
    link = DiagLink(FakePanda({(0x750, 0xB5): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/B5"), [setting])
    assert result["items"] == [] and result["supported"] == 0

  def test_toyota_can_setting_is_read_from_expanded_data(self):
    window = FakeBodyEcu({0x11: bytes.fromhex("a020")}, {0x11: bytes.fromhex("ff0030")})
    link = DiagLink(FakePanda({(0x750, 0x90): window}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/90"), [WINDOW_SETTING])
    # 压缩 a0 20 按掩码 ff 00 30 展开成 a0 00 20，第 2 字节 & 0x20 → 1
    assert result["items"] == [{"key": "car_setting_one_touch_test", "value": 1, "raw": "a00020"}]

  def test_offline_ecu(self):
    link = DiagLink(FakePanda({}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/40"), _settings_of("750/40"))
    assert (result["status"], result["items"], result["total"]) == ("offline", [], 44)

  def test_unreadable_did_is_reported_not_raised(self):
    fake = FakeUdsEcu.from_probe(PROBE, "750", "40")
    del fake.memory[0x2270]                                             # 位图说支持，读却被拒
    link = DiagLink(FakePanda({(0x750, 0x40): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/40"), _settings_of("750/40"))
    assert result["read"] == 43 and result["readError"] == "NRC 31"
