#!/usr/bin/env python3
"""Tests for tools/release/release_files.py.

设备上没有 Node，前端只以构建产物（openpilot/system/lanlinkd/static/）的形式
发布。这里守住那条边界：前端源码与依赖清单留在仓库供开发，但不进设备镜像。
"""
from __future__ import annotations

import re
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


class TestFrontendBuildIsCurrent(unittest.TestCase):
  """产物随 git 提交，就必须防住"改了源码忘了构建"。

  这个失败模式很安静：release 照常打包，设备上跑的是旧界面，没有任何报错。
  """

  def test_dist_matches_committed_sources(self):
    r = subprocess.run(
      ["python3", str(REPO_ROOT / "tools/lanlink/build_lanlink_web.py"), "--check"],
      cwd=REPO_ROOT, capture_output=True, text=True,
    )
    self.assertEqual(r.returncode, 0, f"前端产物与源码不一致：\n{r.stdout}\n{r.stderr}")

  def test_build_hash_ships_with_the_release(self):
    # 指纹文件本身要发到设备，否则设备上无从校验
    self.assertIn("openpilot/system/lanlinkd/static/.build-hash", release_payload())

  def test_no_orphaned_assets_are_tracked(self):
    """assets/ 里不该有 index.html 没引用的文件。

    Vite 文件名带内容 hash，而 outDir 是 emptyOutDir:false，旧产物会堆积。
    一旦被 git add，就会**永久**随每个 release 发到设备，还带 immutable
    长缓存。tools/lanlink/build_lanlink_web.py 会在构建后清理。
    """
    static = REPO_ROOT / "openpilot/system/lanlinkd/static"
    index = (static / "index.html").read_text()
    referenced = set(re.findall(r"/assets/([A-Za-z0-9._\-]+)", index))
    self.assertTrue(referenced, "index.html 没有引用任何 assets")

    tracked_assets = {
      Path(p).name for p in tracked_files()
      if p.startswith("openpilot/system/lanlinkd/static/assets/")
    }
    self.assertEqual(
      tracked_assets - referenced, set(),
      "存在已失效但仍被 git 跟踪的产物；运行 tools/lanlink/build_lanlink_web.py --build",
    )


if __name__ == "__main__":
  unittest.main()
