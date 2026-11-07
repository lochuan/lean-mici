#!/usr/bin/env python3
"""把 Techstream 元数据整理出的实时数据项和 ECU 叠加进 Sienna 4 代目录 `toyota/data/sienna_catalog.json`。

为什么是第二数据源：目录主体由 `build_toyota_sienna_catalog.py` 从 Carista 快照生成，
但快照和翻译不在仓库里（已删除），生成脚本无法重跑。Techstream 这批数据
（kaikozlov/toyota-diagnostics 元数据，Sienna HV，未上车验证）存在
`tools/lanlink/sienna_techstream_items.json`，用本脚本叠加到已生成的目录上。

叠加规则：ecus 按 id、live 按 key、liveTools 按 id 去重，重复则覆盖（位置保持不变），所以可重复运行。
只增改、不删除：删项需手工从目录里删掉（再重跑本脚本不会把它加回来，除非数据文件里还有）。
live 条目的 `source`（参考公式出处）只留在数据文件里，写进目录时去掉，键顺序对齐现有条目。

用法：
  tools/lanlink/merge_sienna_techstream_items.py <sienna_techstream_items.json> <sienna_catalog.json>
"""
import json
import sys

# 目录里 live 条目的键与顺序（与 build_toyota_sienna_catalog.py 生成的一致）
LIVE_KEYS = ["key", "name", "ecu", "did", "byteIndex", "mask", "mult", "offset", "signed", "precision", "unit", "options", "needsEngineRunning"]


def _upsert(entries: list[dict], new_entries: list[dict], id_field: str) -> None:
  positions = {entry[id_field]: i for i, entry in enumerate(entries)}
  for new in new_entries:
    if new[id_field] in positions:
      entries[positions[new[id_field]]] = new
    else:
      positions[new[id_field]] = len(entries)
      entries.append(new)


def merge(items: dict, catalog: dict) -> None:
  live = [{k: item[k] for k in LIVE_KEYS} for item in items["live"]]
  _upsert(catalog["ecus"], items["ecus"], "id")
  _upsert(catalog["live"], live, "key")
  _upsert(catalog["liveTools"], items["liveTools"], "id")


def main(items_path: str, catalog_path: str) -> None:
  with open(items_path) as f:
    items = json.load(f)
  with open(catalog_path) as f:
    catalog = json.load(f)
  merge(items, catalog)
  with open(catalog_path, "w") as f:
    json.dump(catalog, f, ensure_ascii=False, indent=1)
    f.write("\n")
  print(f"{len(catalog['ecus'])} 个 ECU，{len(catalog['live'])} 项实时数据，{len(catalog['liveTools'])} 个实时工具 → {catalog_path}")


if __name__ == "__main__":
  if len(sys.argv) != 3:
    sys.exit(__doc__)
  main(*sys.argv[1:])
