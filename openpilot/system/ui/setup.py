#!/usr/bin/env python3
import os
# AGNOS factory setup 以 zipapp 运行，openpilot 原生库（libparams_c 等）不存在；
# sunnypilot 控件会导入 Params，必须在导入任何 UI 模块前关掉，退回原版控件。
os.environ.setdefault("SUNNYPILOT_UI", "0")

from openpilot.system.ui.lib.application import gui_app
import openpilot.system.ui.tici_setup as tici_setup
import openpilot.system.ui.mici_setup as mici_setup


def main():
  if gui_app.big_ui():
    tici_setup.main()
  else:
    mici_setup.main()


if __name__ == "__main__":
  main()
