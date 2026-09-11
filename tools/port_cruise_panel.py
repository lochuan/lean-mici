#!/usr/bin/env python3
"""Port upstream's `cruise` settings panel into the local LANLink schema.

Our settings_ui.json was assembled before upstream had a cruise panel, so the
whole longitudinal surface (experimental mode, driving personality, custom ACC
increments, speed limit assist, smart cruise) was simply missing from the web
UI even though all 14 params exist on the device -- verified against the device
param store, not assumed.

Rules (`enablement`/`visibility`/`trigger_condition`) are copied verbatim; only
human-facing strings are translated, to match the rest of the local schema.
Translating a rule would silently change which toggles the device offers.

Run once; it is idempotent (re-running replaces the cruise panel in place).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "openpilot/system/lanlinkd/settings_ui.json"
UPSTREAM_PATH = "openpilot/sunnypilot/sunnylink/settings_ui.json"

# 仅翻译展示文案。key/widget/规则一律不动。
TITLES = {
  "Experimental Mode": "实验模式",
  "Dynamic Experimental Control": "动态实验控制",
  "Disengage Cruise on Accelerator Pedal": "踩油门时退出纵向控制",
  "Driving Personality": "驾驶风格",
  "Intelligent Cruise Button Management (ICBM) (Alpha)": "智能巡航按键管理 (ICBM) (Alpha)",
  "Enable Custom ACC Speed Intervals": "启用自定义 ACC 速度步进",
  "Short Press Increment": "短按步进",
  "Long Press Increment": "长按步进",
  "Speed Limit Assist Mode": "限速辅助模式",
  "Speed Limit Source": "限速数据来源",
  "Speed Limit Offset Type": "限速偏移类型",
  "Speed Limit Offset Value": "限速偏移值",
  "Vision": "视觉",
  "Map": "地图",
}

SECTION_TITLES = {
  "": "",
  "Custom ACC Speed Intervals": "自定义 ACC 速度步进",
  "Speed Limits": "限速",
  "Smart Cruise Control": "智能巡航控制",
}

SUB_PANEL_LABELS = {
  "Custom ACC Speed Intervals Settings": "自定义 ACC 速度步进设置",
  "Speed Limit Settings": "限速设置",
  # panel 自身的 label（侧栏名），与本地其它 panel 的中文标签一致
  "Cruise": "巡航",
}

DESCRIPTIONS = {
  "Let the model decide when to use sunnypilot ACC or sunnypilot End to End Longitudinal.":
    "由模型决定何时使用 sunnypilot ACC 或端到端纵向控制。",
  "When enabled, pressing the accelerator pedal will disengage longitudinal control.":
    "启用后，踩下油门踏板将退出纵向控制。",
  "Standard is recommended. In aggressive mode, sunnypilot will follow lead cars closer and be more "
  "aggressive with the gas and brake. In relaxed mode sunnypilot will stay further away from lead cars. "
  "On supported cars, you can cycle through these personalities with your steering wheel distance button.":
    "推荐标准。激进模式下跟车更近、油门刹车更积极；放松模式下与前车保持更远距离。"
    "在支持的车型上，可用方向盘车距键循环切换。",
  "Use vision path predictions to estimate the appropriate speed to drive through turns ahead.":
    "使用视觉路径预测，估算通过前方弯道的合适速度。",
  "Use map data to estimate the appropriate speed to drive through turns ahead.":
    "使用地图数据，估算通过前方弯道的合适速度。",
  "Speed limit detection and offset behavior": "限速识别与偏移行为",
  "Longitudinal control, speed limits, and cruise behavior": "纵向控制、限速与巡航行为",
}

OPTION_LABELS = {
  "Aggressive": "激进", "Standard": "标准", "Relaxed": "放松",
  "Off": "关闭", "Information": "仅提示", "Warning": "警告", "Assist": "辅助",
  "Car State Only": "仅车辆信号", "Map Data Only": "仅地图数据",
  "Car State Priority": "车辆信号优先", "Map Data Priority": "地图数据优先",
  "Combined": "综合", "Fixed": "固定值", "Percentage": "百分比",
}


def _fetch_upstream() -> dict:
  raw = subprocess.run(
    ["git", "show", f"upstream/master:{UPSTREAM_PATH}"],
    cwd=REPO, check=True, text=True, capture_output=True,
  ).stdout
  return json.loads(raw)


def _translate(node):
  """Walk the panel, translating only display strings."""
  if isinstance(node, list):
    return [_translate(v) for v in node]
  if not isinstance(node, dict):
    return node

  out = {}
  for k, v in node.items():
    if k == "title":
      out[k] = TITLES.get(v, SECTION_TITLES.get(v, v))
    elif k == "label":
      out[k] = SUB_PANEL_LABELS.get(v, OPTION_LABELS.get(v, v))
    elif k == "description":
      out[k] = DESCRIPTIONS.get(v, v)
    else:
      out[k] = _translate(v)
  return out


def _untranslated(node) -> list[tuple[str, str]]:
  """Display strings still pure ASCII, i.e. missed by the translation tables.

  Without this check a missing table entry ships silently as English text in an
  otherwise Chinese UI, and nobody notices until it is on the device.
  """
  found: list[tuple[str, str]] = []

  def walk(o):
    if isinstance(o, dict):
      for k, v in o.items():
        if k in ("title", "label", "description") and isinstance(v, str) and v.strip():
          if all(ord(c) < 128 for c in v):
            found.append((k, v))
        else:
          walk(v)
    elif isinstance(o, list):
      for v in o:
        walk(v)

  walk(node)
  return found


def main() -> int:
  local = json.loads(LOCAL.read_text())
  upstream = _fetch_upstream()

  cruise = next((p for p in upstream["panels"] if p["id"] == "cruise"), None)
  if cruise is None:
    print("upstream has no cruise panel", file=sys.stderr)
    return 1

  ported = _translate(cruise)
  # 本地 panel 用 order 排序；cruise 紧随 steering(1)
  ported["order"] = 2

  missed = _untranslated(ported)
  if missed:
    for kind, text in missed:
      print(f"untranslated {kind}: {text}", file=sys.stderr)
    print("add the strings above to the translation tables, then re-run", file=sys.stderr)
    return 1

  panels = [p for p in local["panels"] if p["id"] != "cruise"]
  panels.append(ported)
  panels.sort(key=lambda p: (p.get("order", 99), p["id"]))
  local["panels"] = panels

  LOCAL.write_text(json.dumps(local, ensure_ascii=False, indent=1) + "\n")

  items = sum(len(s.get("items", [])) + sum(len(sp.get("items", [])) for sp in s.get("sub_panels", []))
              for s in ported["sections"])
  print(f"ported cruise panel: {len(ported['sections'])} sections, {items} items")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
