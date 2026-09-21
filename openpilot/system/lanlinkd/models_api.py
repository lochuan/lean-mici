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
CACHE_KEY = "ModelManager_ModelsCache"      # JSON {"tinygrad_ref": ..., "bundles": [snake_case...]}
DOWNLOAD_REF_KEY = "ModelManager_DownloadRef"  # STRING ref
LAST_SYNC_KEY = "ModelManager_LastSyncTime"   # INT monotonic ns；0 = 立即过期重抓
CLEAR_CACHE_KEY = "ModelManager_ClearCache"   # BOOL
FAVS_KEY = "ModelManager_Favs"                # STRING，";" 分隔 ref


def _device_tinygrad_ref() -> str | None:
  """本机树 tinygrad_repo 的 HEAD。文件级读取（规则复制自
  sunnypilot/models/tinygrad_ref.py），不走 sunnypilot.models import——
  那条链会拉 common.params（libparams_c），web 层在 PC/测试环境会 OSError。"""
  repo = os.path.join(os.path.dirname(__file__), "..", "..", "..", "tinygrad_repo")
  git_path = os.path.normpath(repo + "/.git")
  try:
    if os.path.isdir(git_path):
      head = os.path.join(git_path, "HEAD")
    else:
      with open(git_path) as f:
        head = os.path.join(repo, f.read().strip()[8:])
    ref = open(os.path.join(head)).read().strip() if os.path.isfile(head) else ""
    if ref.startswith("ref:"):
      ref = open(os.path.normpath(os.path.join(git_path, ref.split(" ", 1)[1]))).read().strip()
    return ref or None
  except OSError:
    return None


def _catalog_tinygrad_ref(cache) -> str | None:
  return (cache.get("tinygrad_ref") or None) if isinstance(cache, dict) else None


def _pin_verdict(catalog_ref: str | None, device_ref: str | None) -> bool | None:
  """True/False = 可判定；None = 任一侧未知（旧 manifest / 取不到 pin），放行旧行为。"""
  if not catalog_ref or not device_ref:
    return None
  return catalog_ref == device_ref


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
  """取分组名（车内 UI 也按它分组，见 ui/sunnypilot/mici/layouts/models.py）。

  overrides 在缓存 JSON 里是**字典**（实测：{"folder": "Legacy Models",
  "lat": ".0", "long": ".3"}），不是 [{key,value}] 列表。而 capnp 的
  bundle.overrides 才是带 .key/.value 的列表——两种形态都要认，否则分组名
  全是空字符串，77 个模型会挤成一个没有名字的组。
  """
  ov = b.get("overrides") or {}
  if isinstance(ov, dict):
    return str(ov.get("folder", "") or "")
  return next((o.get("value", "") for o in ov
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
  catalog_ref = _catalog_tinygrad_ref(cache)
  device_ref = _device_tinygrad_ref()
  verdict = _pin_verdict(catalog_ref, device_ref)
  pin_mismatch_detail = None
  if verdict is False:
    pin_mismatch_detail = (
      f"模型目录基于 tinygrad {catalog_ref[:8]} 编译，本机为 {device_ref[:8]}，"
      f"目录里的模型不可下载；请升级系统使 tinygrad 对齐后刷新列表"
    )
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
      "pinCompatible": verdict,
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
    "pin_mismatch_detail": pin_mismatch_detail,
  }


def select(store, ref: str) -> tuple[int, str]:
  if not ref:
    return 400, "ref required"
  if ref == "Default":
    store.remove(ACTIVE_KEY)
    _bump(store)
    return 204, ""
  cache = store.get(CACHE_KEY)
  bundles = _bundles_from_cache(cache)
  refs = {b.get("ref") for b in bundles}
  if ref not in refs:
    return 404, "unknown model ref"
  # pin 门控：目录与本机 tinygrad 不一致时点下载必然产出 modeld 崩溃的 pkl
  # （位置 pickle 契约），在这里直接拒绝并给出原因，而不是排队后静默无操作。
  verdict = _pin_verdict(_catalog_tinygrad_ref(cache), _device_tinygrad_ref())
  if verdict is False:
    return 409, (
      f"「{next(b.get('display_name', ref) for b in bundles if b.get('ref') == ref)}」"
      f"基于 tinygrad {_catalog_tinygrad_ref(cache)[:8]} 编译，本机为 {_device_tinygrad_ref()[:8]}，"
      f"不兼容不可下载；请升级系统对齐后再试"
    )
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
