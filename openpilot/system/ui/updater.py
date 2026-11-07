#!/usr/bin/env python3
import os
# 同 setup.py：AGNOS 更新界面以 zipapp 运行，没有 openpilot 原生库，导入任何 UI 模块前关掉 sunnypilot 控件
os.environ.setdefault("SUNNYPILOT_UI", "0")

from openpilot.system.ui.lib.application import gui_app
import openpilot.system.ui.tici_updater as tici_updater
import openpilot.system.ui.mici_updater as mici_updater


def main():
  if gui_app.big_ui():
    tici_updater.main()
  else:
    mici_updater.main()


if __name__ == "__main__":
  main()
