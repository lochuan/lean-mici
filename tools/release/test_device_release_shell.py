#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import tempfile
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


class TestPinIntact(unittest.TestCase):
  """pin 的 sha 对得上还不够：flat 树 git reset 会把 repo 内文件换回旧内容而 pin 不变，
  只验 canary 在位会把旧 opendbc 当新的发出去（2026-10-04：card 因 structs.py 缺字段起不来）。"""

  def _check(self, mutate=None) -> bool:
    fn = re.search(r"^pin_intact\(\) \{.*?^\}", DEVICE.read_text(), re.M | re.S)
    mat = re.search(r"^write_pin\(\) \{.*?^\}", DEVICE.read_text(), re.M | re.S)
    self.assertTrue(fn and mat, "pin_intact / write_pin 必须定义")
    with tempfile.TemporaryDirectory() as d:
      src, pins = Path(d, "src"), Path(d, "pins")
      (src / "repo").mkdir(parents=True)
      (src / "repo" / "structs.py").write_text("new")
      head = f'PIN_DIR="{pins}"; SRC="{src}"\n{mat.group(0)}\n{fn.group(0)}\n'
      subprocess.run(["bash", "-c", head + 'write_pin repo SHA "$SRC/repo"'], check=True)
      if mutate:
        (src / "repo" / "structs.py").write_text(mutate)
      return subprocess.run(["bash", "-c", head + 'pin_intact repo SHA']).returncode == 0

  def test_intact(self):
    self.assertTrue(self._check())

  def test_stale_content_with_matching_sha_is_not_intact(self):
    self.assertFalse(self._check(mutate="old"))


class TestPklCacheHit(unittest.TestCase):
  """driving pkl 编完即切块、原文件删掉：缓存判定必须认切块产物，否则每次发版白编 ~11 分钟。"""

  def _hit(self, files: dict[str, str], fp="FP") -> bool:
    fn = re.search(r"^pkl_cache_hit\(\) \{.*?^\}", DEVICE.read_text(), re.M | re.S).group(0)
    with tempfile.TemporaryDirectory() as d:
      for name, body in files.items():
        Path(d, name).write_text(body)
      return subprocess.run(["bash", "-c", f'{fn}\npkl_cache_hit "$0/m.pkl" "$1"', d, fp]).returncode == 0

  def test_whole_pkl_hit(self):
    self.assertTrue(self._hit({"m.pkl": "x", "m.pkl.inputs_fp": "FP"}))

  def test_chunked_pkl_hit(self):
    self.assertTrue(self._hit({"m.pkl.chunkmanifest": "3", "m.pkl.inputs_fp": "FP"}))

  def test_miss_on_changed_fingerprint_or_no_artifact(self):
    self.assertFalse(self._hit({"m.pkl": "x", "m.pkl.inputs_fp": "OLD"}))
    self.assertFalse(self._hit({"m.pkl.inputs_fp": "FP"}))


if __name__ == "__main__":
  unittest.main()
