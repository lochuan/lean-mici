import pytest

from openpilot.system.lanlinkd.toyota import customization as cz
from openpilot.system.lanlinkd.toyota.backups import BackupStore
from openpilot.system.lanlinkd.toyota.link import DiagLink

from .toyota_fakes import FakeBodyEcu, FakePanda
from .test_toyota_customization import ITEM

OTHER_ECU_ITEM = {**ITEM, "uid": 2, "tx": "7C4", "sub": None, "readId": "81", "writeId": "81"}


def make(memory=None, reject_writes=None, tmp_path=None):
  ecu = FakeBodyEcu(memory or {0x12: bytes.fromhex("a020")}, {0x12: bytes.fromhex("ff0030")}, reject_writes)
  link = DiagLink(FakePanda({(0x750, 0x40): ecu}), elm327_mode=3, heartbeat_thread=False, timeout=0.02, pending_timeout=0.1)
  return ecu, link, BackupStore(str(tmp_path)) if tmp_path else None


class TestScan:
  def test_lists_supported_items_with_current_value(self):
    _, link, _ = make()
    items = cz.scan(link, [ITEM, OTHER_ECU_ITEM])
    # 0x7C4 不在线 → 该 ECU 的项不出现
    assert [i["uid"] for i in items] == [1]
    assert items[0]["current"] == 0
    assert items[0]["raw"] == "a020"


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

  def test_restore_writes_backup_back(self, tmp_path):
    ecu, link, store = make(tmp_path=tmp_path)
    backup_id = cz.write(link, ITEM, 1, "a020", store)["backupId"]
    assert cz.restore(link, store.get(backup_id)) == {"verified": True}
    assert ecu.memory[0x12] == bytes.fromhex("a020")
