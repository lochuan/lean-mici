#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

DEVICE = Path(__file__).resolve().parent / "device_release.sh"


class TestDeviceReleaseShellBoilerplate(unittest.TestCase):
  """壳样板唯一定义断言（fix/release-stages ①）。

  CPU 唤醒段 3 处逐字重复、fetch 重试 3 份、失败包装 9 处手写——下个阶段
  的人会照抄上个阶段。帮助函数存在 + 手写样板绝迹，源码级断言（照
  test_is_run_model 手法）：抄回一份就红。
  """

  @classmethod
  def setUpClass(cls):
    cls.src = DEVICE.read_text()

  def test_helpers_are_defined(self):
    for fn in ("wake_cpu_cores", "git_fetch_retry", "run_stage"):
      self.assertIn(f"{fn}()", self.src)

  def test_cpu_wake_loop_lives_only_in_helper(self):
    self.assertEqual(self.src.count("for n in 4 5 6 7"), 1,
                     "CPU 唤醒段必须只在 wake_cpu_cores 里出现一次")

  def test_fetch_retry_loop_lives_only_in_helper(self):
    self.assertEqual(self.src.count("for i in 1 2 3"), 1,
                     "fetch 重试循环必须只在 git_fetch_retry 里出现一次")

  def test_no_handwritten_failure_wrappers(self):
    self.assertNotIn("|| { echo", self.src, "失败话术走 run_stage/die，不得手写 || { echo; exit 1; }")
    self.assertNotIn("exit 1; }", self.src)

  def test_stage_headers_use_run_stage(self):
    self.assertIn('run_stage "', self.src)
    self.assertGreaterEqual(self.src.count("run_stage \""), 5)


if __name__ == "__main__":
  unittest.main()
