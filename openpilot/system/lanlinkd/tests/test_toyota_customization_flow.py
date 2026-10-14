import os

import pytest

from openpilot.system.lanlinkd.toyota import catalog
from openpilot.system.lanlinkd.toyota import customization as cz
from openpilot.system.lanlinkd.toyota.backups import BackupStore
from openpilot.system.lanlinkd.toyota.link import DiagLink, EcuRejectedError

from .toyota_fakes import FakeToyotaCanEcu, FakeEcu, FakePanda, FakeUdsEcu

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
    window = FakeToyotaCanEcu({0x11: bytes.fromhex("a020")}, {0x11: bytes.fromhex("ff0030")})
    link = DiagLink(FakePanda({(0x750, 0x90): window}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/90"), [WINDOW_SETTING])
    # 压缩 a0 20 按掩码 ff 00 30 展开成 a0 00 20，第 2 字节 & 0x20 → 1
    assert result["items"] == [{"key": "car_setting_one_touch_test", "value": 1, "raw": "a00020"}]

  def test_malformed_ecu_is_reported_not_raised(self):
    # 实车：750/AD 天窗的 A8 03 应答序号错乱曾让整个扫描变成 HTTP 500
    sunroof = FakeEcu({b"\x3e": b"\x7e", b"\xa8\x03": b"\xe8\x03" + bytes(range(20))}, misnumbered_frames=True)
    link = DiagLink(FakePanda({(0x750, 0xAD): sunroof}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/AD"), [])
    assert result["status"] == "malformed" and "invalid consecutive frame index" in result["malformedReason"]

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

  def test_unreadable_support_bitmap_is_reported(self):
    fake = FakeUdsEcu({0x2270: b"\x01"}, {b"\x3e": b"\x7e"})            # 22 2200 被拒：不知道哪些项支持
    link = DiagLink(FakePanda({(0x750, 0x40): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.scan_ecu(link, catalog.ecu("750/40"), _settings_of("750/40"))
    assert (result["status"], result["items"], result["readError"]) == ("ok", [], "NRC 31")


HEADLIGHTS = "car_setting_warn_headlights_on_door_open"   # 750/40 DID 2270 字节 0 掩码 FF，实车值 01


def _body_ecu_link(**fake_kwargs):
  fake = FakeUdsEcu.from_probe(PROBE, "750", "40", **fake_kwargs)
  link = DiagLink(FakePanda({(0x750, 0x40): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return fake, link


class TestWriteSetting:
  def test_uds_write_backs_up_writes_and_verifies(self, tmp_path):
    fake, link = _body_ecu_link()
    backups = BackupStore(str(tmp_path))
    result = cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", backups)
    assert {k: result[k] for k in ("key", "value", "verified", "before", "after")} == \
      {"key": HEADLIGHTS, "value": 0, "verified": True, "before": "01", "after": "00"}
    assert fake.memory[0x2270] == b"\x00"
    saved = backups.get(result["backupId"])
    assert (saved["key"], saved["ecu"], saved["data"], saved["after"]) == (HEADLIGHTS, "750/40", "01", "00")

  def test_refuses_when_ecu_changed_since_scan(self, tmp_path):
    fake, link = _body_ecu_link()
    with pytest.raises(cz.StaleDataError):
      cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "00", BackupStore(str(tmp_path)))
    assert fake.memory[0x2270] == b"\x01" and not os.listdir(tmp_path)

  def test_switches_to_extended_session_when_default_session_refuses(self, tmp_path):
    fake, link = _body_ecu_link(writes_need_extended_session=True)
    result = cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", BackupStore(str(tmp_path)))
    assert result["verified"] and fake.extended_session
    assert fake.requests.count(b"\x10\x03") == 1

  def test_rejected_write_raises_with_nrc(self, tmp_path):
    _, link = _body_ecu_link(reject_writes=b"\x7f\x2e\x22")
    with pytest.raises(EcuRejectedError) as exc:
      cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", BackupStore(str(tmp_path)))
    assert exc.value.nrc == 0x22

  def test_failed_backup_blocks_write(self, tmp_path):
    fake, link = _body_ecu_link()
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(cz.BackupError):
      cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", BackupStore(str(blocker / "sub")))
    assert fake.memory[0x2270] == b"\x01"

  def test_unverified_write_reports_data_read_back_from_ecu(self, tmp_path):
    fake, link = _body_ecu_link(ignore_writes=True)
    result = cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", BackupStore(str(tmp_path)))
    assert (result["verified"], result["value"], result["after"]) == (False, 1, "01")

  def test_toyota_can_write_sends_expanded_data(self, tmp_path):
    window = FakeToyotaCanEcu({0x11: bytes.fromhex("a020")}, {0x11: bytes.fromhex("ff0030")})
    link = DiagLink(FakePanda({(0x750, 0x90): window}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    result = cz.write_setting(link, catalog.ecu("750/90"), WINDOW_SETTING, 0, "a00020", BackupStore(str(tmp_path)))
    assert (result["verified"], result["value"], result["after"]) == (True, 0, "a00000")
    assert window.memory[0x11] == bytes.fromhex("a000")
    assert bytes.fromhex("3b11a00000") in window.requests

  def test_restore_backs_up_current_value_then_writes_backup_back(self, tmp_path):
    fake, link = _body_ecu_link()
    backups = BackupStore(str(tmp_path))
    written = cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", backups)
    restored = cz.restore_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), backups.get(written["backupId"]), backups)
    assert restored["verified"] and restored["value"] == 1
    assert fake.memory[0x2270] == b"\x01"
    assert backups.get(restored["backupId"])["data"] == "00"        # 恢复前另存的是被覆盖的值
    assert backups.get(written["backupId"])["restored"] is True

  def test_unverified_restore_keeps_backup_restorable(self, tmp_path):
    fake, link = _body_ecu_link()
    backups = BackupStore(str(tmp_path))
    written = cz.write_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), 0, "01", backups)
    fake.ignore_writes = True
    restored = cz.restore_setting(link, catalog.ecu("750/40"), catalog.setting(HEADLIGHTS), backups.get(written["backupId"]), backups)
    assert not restored["verified"]
    assert not backups.get(written["backupId"]).get("restored")

  def test_undo_restores_only_its_own_bits(self, tmp_path):
    # 750/B5 DID 2009 实车 c0 00：字节 1 的 0x80 / 0x40 是两个不同的设置项
    fake = FakeUdsEcu.from_probe(PROBE, "750", "B5")
    link = DiagLink(FakePanda({(0x750, 0xB5): fake}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
    backups = BackupStore(str(tmp_path))
    ecu = catalog.ecu("750/B5")
    closed = catalog.setting("car_setting_auto_arm_on_doors_closed")
    closing = catalog.setting("car_setting_auto_arm_on_doors_closing")
    first = cz.write_setting(link, ecu, closed, 1, "c000", backups)
    cz.write_setting(link, ecu, closing, 1, "c080", backups)
    restored = cz.restore_setting(link, ecu, closed, backups.get(first["backupId"]), backups)
    assert fake.memory[0x2009] == bytes.fromhex("c040")             # 后写的 closing 不被一起改回
    assert restored["verified"] and restored["value"] == 0
