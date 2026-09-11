#!/usr/bin/env python3
"""Tests for tools/release/release_files.py.

设备上没有 Node，前端只以构建产物（openpilot/system/lanlinkd/static/）的形式
发布。这里守住那条边界：前端源码与依赖清单留在仓库供开发，但不进设备镜像。
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().parent / "release_files.py"

WEB_DIR = "openpilot/system/lanlinkd/web/"
STATIC_DIR = "openpilot/system/lanlinkd/static/"


def release_payload() -> list[str]:
  """release 实际会打包的文件列表。"""
  out = subprocess.check_output(["python3", str(SCRIPT)], cwd=REPO_ROOT)
  return [p for p in out.decode().split("\0") if p]


def tracked_files() -> list[str]:
  out = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO_ROOT)
  return [p for p in out.decode().split("\0") if p]


class TestFrontendPackagingBoundary(unittest.TestCase):
  @classmethod
  def setUpClass(cls) -> None:
    cls.payload = release_payload()
    cls.tracked = tracked_files()

  def test_frontend_sources_are_not_shipped_to_device(self):
    """web/ 下的源码/配置/依赖清单都不该进设备（设备没有 Node）。"""
    leaked = [p for p in self.payload if p.startswith(WEB_DIR)]
    self.assertEqual(leaked, [], f"前端源码泄漏进 release 包: {leaked}")

  def test_node_modules_never_enters_git_or_release(self):
    """框架与依赖代码既不入库也不上设备。"""
    self.assertEqual([p for p in self.tracked if "node_modules" in p], [])
    self.assertEqual([p for p in self.payload if "node_modules" in p], [])

  def test_built_frontend_is_shipped(self):
    """设备要真的拿到构建产物，否则界面打不开。"""
    shipped = [p for p in self.payload if p.startswith(STATIC_DIR)]
    self.assertGreater(len(shipped), 0, "release 包里没有任何前端产物")
    self.assertIn(f"{STATIC_DIR}index.html", shipped)

  def test_frontend_sources_stay_in_the_repo(self):
    """源码要保留在 git 里——只是不发到设备，不是删掉。"""
    for f in ("package.json", "package-lock.json", "vite.config.ts"):
      self.assertIn(f"{WEB_DIR}{f}", self.tracked, f"{f} 应保留在仓库中作为依赖/构建说明")
    self.assertTrue(
      any(p.startswith(f"{WEB_DIR}src/") for p in self.tracked),
      "前端 src/ 源码应保留在仓库中",
    )

  def test_backend_still_ships(self):
    """别把整个 lanlinkd 一起排除掉了。"""
    self.assertIn("openpilot/system/lanlinkd/lanlinkd.py", self.payload)
    self.assertIn("openpilot/system/lanlinkd/settings_ui.json", self.payload)


if __name__ == "__main__":
  unittest.main()
