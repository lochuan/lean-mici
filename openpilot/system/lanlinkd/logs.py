"""日志目录列表与文件解析（只读）。"""
import os


def list_routes(log_root: str) -> list[dict]:
  routes = []
  if not os.path.isdir(log_root):
    return routes
  for entry in os.scandir(log_root):
    if not entry.is_dir():
      continue
    total, segments = 0, 0
    for root, _, files in os.walk(entry.path):
      for f in files:
        total += os.path.getsize(os.path.join(root, f))
        if f.startswith(("qlog", "qcam", "rlog", "fcam", "dcam", "ecam")):
          segments += 1
    routes.append({"route": entry.name, "mtime": int(entry.stat().st_mtime),
                   "size": total, "segments": segments})
  return sorted(routes, key=lambda r: r["mtime"], reverse=True)


def resolve_log_file(log_root: str, route: str, fname: str) -> str | None:
  base = os.path.realpath(log_root)
  full = os.path.realpath(os.path.join(base, route, fname))
  if not full.startswith(base + os.sep):
    return None
  if not os.path.isfile(full):
    return None
  return full
