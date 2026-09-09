"""模型管理 API 纯逻辑。store 为 duck-type Params（.get/.put/.put_bool/.remove）。

不 import sunnypilot/models/{fetcher,helpers}：它们模块级 import common.params
（依赖 libparams_c，仅设备存在），web 层在 PC/测试环境加载会 OSError。
解析规则/常量复制自源码并注释出处：
- REQUIRED_JSON_VERSION 与目录过滤：helpers.py:18 + fetcher.parse_models
- 目录 JSON 键为 snake_case（sunnypilot-models 仓库 manifest）
- ActiveBundle param 为 camelCase（manager.py: model_bundle.to_dict()）
"""
import os

DEFAULT_MODEL = "CD210"  # 复制自 sunnypilot/models/model_name.py:1
REQUIRED_JSON_VERSION = 19  # 复制自 sunnypilot/models/helpers.py:18

ACTIVE_KEY = "ModelManager_ActiveBundle"    # JSON camelCase ModelBundle
CACHE_KEY = "ModelManager_ModelsCache"      # JSON {"bundles": [snake_case...]}
DOWNLOAD_REF_KEY = "ModelManager_DownloadRef"  # STRING ref
LAST_SYNC_KEY = "ModelManager_LastSyncTime"   # INT monotonic ns；0 = 立即过期重抓
CLEAR_CACHE_KEY = "ModelManager_ClearCache"   # BOOL
FAVS_KEY = "ModelManager_Favs"                # STRING，";" 分隔 ref


def _bump(store) -> None:
  from openpilot.system.lanlinkd import params_api
  params_api._bump_version(store)


def _bundles_from_cache(cache) -> list[dict]:
  out = []
  for b in (cache if isinstance(cache, dict) else {}).get("bundles", []):
    if not isinstance(b, dict):
      continue
    try:
      # manifest 里 minimum_selector_version 可能是字符串（fetcher 解析时才 int() 转换）
      if int(b.get("minimum_selector_version") or 0) == REQUIRED_JSON_VERSION:
        out.append(b)
    except (TypeError, ValueError):
      continue
  return out


def _folder(b: dict) -> str:
  return next((o.get("value", "") for o in b.get("overrides", []) or []
               if isinstance(o, dict) and o.get("key") == "folder"), "")


def _cache_size_mb(model_root: str) -> float:
  total = 0
  try:
    for fn in os.listdir(model_root):
      path = os.path.join(model_root, fn)
      if os.path.isfile(path):
        total += os.path.getsize(path)
  except OSError:
    return 0.0
  return round(total / (1024 * 1024), 1)


def models_state(store, download: dict | None, model_root: str) -> dict:
  cache = store.get(CACHE_KEY)
  favs_raw = store.get(FAVS_KEY)
  favs = [f for f in str(favs_raw or "").split(";") if f]
  active = store.get(ACTIVE_KEY)
  active = active if isinstance(active, dict) and active else None
  active_ref = (active or {}).get("ref") or ""
  entries = []
  for b in _bundles_from_cache(cache):
    ref = b.get("ref") or ""
    entries.append({
      "ref": ref,
      "displayName": b.get("display_name", ""),
      "internalName": b.get("short_name", ""),
      "index": int(b.get("index") or 0),
      "generation": int(b.get("generation") or 0),
      "environment": b.get("environment", ""),
      "runner": b.get("runner", "snpe"),
      "is20hz": bool(b.get("is_20hz", False)),
      "folder": _folder(b),
      "fav": ref in favs,
      "active": bool(ref and ref == active_ref),
    })
  # 组排序：max(index) 倒序（对齐车内 UI models.py:133）；组内 index 倒序（models.py:162）
  entries.sort(key=lambda b: b["index"], reverse=True)
  return {
    "default_model": DEFAULT_MODEL,
    "active": ({"ref": active_ref, "displayName": active.get("displayName", ""),
                "internalName": active.get("internalName", ""),
                "runner": active.get("runner", "snpe")} if active else None),
    "queued_ref": store.get(DOWNLOAD_REF_KEY) or None,
    "favs": favs,
    "bundles": entries,
    "download": download,
    "cache_size_mb": _cache_size_mb(model_root),
  }


def select(store, ref: str) -> tuple[int, str]:
  if not ref:
    return 400, "ref required"
  if ref == "Default":
    store.remove(ACTIVE_KEY)
    _bump(store)
    return 204, ""
  cache = store.get(CACHE_KEY)
  refs = {b.get("ref") for b in _bundles_from_cache(cache)}
  if ref not in refs:
    return 404, "unknown model ref"
  store.put(DOWNLOAD_REF_KEY, ref, block=True)
  _bump(store)
  return 204, ""


def cancel(store) -> tuple[int, str]:
  store.remove(DOWNLOAD_REF_KEY)
  _bump(store)
  return 204, ""


def refresh(store) -> tuple[int, str]:
  # 置 0 使 ModelCache._is_expired 为 True，manager 下个 tick 重新拉 manifest
  store.put(LAST_SYNC_KEY, 0, block=True)
  _bump(store)
  return 204, ""


def clear_cache(store) -> tuple[int, str]:
  store.put_bool(CLEAR_CACHE_KEY, True, block=True)
  _bump(store)
  return 204, ""


def set_fav(store, ref: str, on: bool) -> tuple[int, str]:
  if not ref:
    return 400, "ref required"
  favs = [f for f in str(store.get(FAVS_KEY) or "").split(";") if f]
  if on and ref not in favs:
    favs.append(ref)
  if not on and ref in favs:
    favs.remove(ref)
  store.put(FAVS_KEY, ";".join(favs), block=True)
  _bump(store)
  return 204, ""
