"""定制写入前的原始字节备份：每次写入一个 JSON 文件，恢复时原样写回。"""
import json
import os
import re
import time
import uuid

_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")


class BackupStore:
  def __init__(self, directory: str):
    self.directory = directory

  def save(self, record: dict) -> str:
    backup_id = uuid.uuid4().hex[:12]
    self._write({**record, "id": backup_id, "time": time.time()})  # noqa: TID251  备份时间给人看，要墙钟
    return backup_id

  def mark_restored(self, backup_id: str) -> None:
    record = self.get(backup_id)
    if record is not None:
      self._write({**record, "restored": True})

  def _write(self, record: dict) -> None:
    os.makedirs(self.directory, exist_ok=True)
    path = os.path.join(self.directory, f"{record['id']}.json")
    with open(path + ".tmp", "w") as f:
      json.dump(record, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)

  def get(self, backup_id: str) -> dict | None:
    if not _ID_PATTERN.match(backup_id):  # 拒绝路径穿越
      return None
    try:
      with open(os.path.join(self.directory, f"{backup_id}.json")) as f:
        return json.load(f)
    except FileNotFoundError:
      return None

  def list(self) -> list[dict]:
    if not os.path.isdir(self.directory):
      return []
    records = (self.get(name[:-5]) for name in os.listdir(self.directory) if name.endswith(".json"))
    return sorted((r for r in records if r), key=lambda r: r["time"], reverse=True)
