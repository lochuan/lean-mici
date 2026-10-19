import os

from openpilot.system.lanlinkd.logs import list_routes, resolve_log_file


def make_tree(tmp_path):
  r1 = tmp_path / "2026-09-06--10-00-00"
  r2 = tmp_path / "2026-09-06--11-00-00"
  (r1 / "1").mkdir(parents=True)
  (r2 / "1").mkdir(parents=True)
  (r1 / "1" / "qlog.zst").write_bytes(b"x" * 100)
  (r2 / "1" / "qlog.zst").write_bytes(b"x" * 300)
  os.utime(r1, (1000, 1000))
  os.utime(r2, (2000, 2000))


def test_list_routes_sorted_desc(tmp_path):
  make_tree(tmp_path)
  routes = list_routes(str(tmp_path))
  assert [r["route"] for r in routes] == ["2026-09-06--11-00-00", "2026-09-06--10-00-00"]
  assert routes[0]["size"] == 300 and routes[0]["segments"] == 1


def test_resolve_log_file_ok_and_traversal(tmp_path):
  make_tree(tmp_path)
  ok = resolve_log_file(str(tmp_path), "2026-09-06--11-00-00", "1/qlog.zst")
  assert ok is not None and ok.endswith("qlog.zst")
  assert resolve_log_file(str(tmp_path), "..", "etc/passwd") is None
  assert resolve_log_file(str(tmp_path), "2026-09-06--11-00-00", "../../etc/passwd") is None
