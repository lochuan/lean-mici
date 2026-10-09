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

  def test_fetch_failure_must_reject(self):
    """fetch 失败 = 拒发：|| true 会吞掉失败继续用旧 ref 发版（2026-10-08 schema 门禁误放行实录）。"""
    fn = re.search(r"^sync_sources\(\) \{.*?^\}", self.src, re.M | re.S)
    self.assertTrue(fn, "sync_sources 必须定义")
    body = fn.group(0)
    self.assertNotIn("|| true", body, "sync_sources 里 fetch 失败不得被 || true 吞掉")
    self.assertIn("ls-remote", body, "必须比对远端 tip 防止旧 ref 发版")

  def test_remote_tip_ignores_branches_with_same_suffix(self):
    """ls-remote 按后缀匹配：origin 另有 vultr/lean-master 时只认 refs/heads/lean-master（2026-10-09 误拒发版实录）。"""
    line = re.search(r'remote_sha=\$\((.*)\) \\$', self.src, re.M)
    self.assertTrue(line, "sync_sources 必须用 remote_sha=$(...) 取远端 tip")
    with tempfile.TemporaryDirectory() as td:
      def git(*args, cwd=td):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()
      origin = f"{td}/origin"
      git("init", "-q", "-b", "lean-master", origin)
      git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a", cwd=origin)
      want = git("rev-parse", "HEAD", cwd=origin)
      git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "b", cwd=origin)
      git("branch", "vultr/lean-master", cwd=origin)
      git("reset", "-q", "--hard", want, cwd=origin)
      git("clone", "-q", origin, f"{td}/clone")
      got = subprocess.run(["bash", "-c", line.group(1)], cwd=f"{td}/clone", env={"SRC_BRANCH": "lean-master", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
                           check=True, capture_output=True, text=True).stdout.strip()
      self.assertEqual(got, want)

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


class TestPublishRunsSmoke(unittest.TestCase):
  """发布末尾必须跑台架冒烟：部署成功不等于 onroad 能起来（card 启动即崩那次，发布脚本照样报 ok）。"""

  def setUp(self):
    self.sh = (DEVICE.parent / "publish_release_from_device.sh").read_text()

  def test_smoke_is_last_step_and_skippable(self):
    self.assertIn("tools/bench/smoke_after_build.sh", self.sh)
    self.assertIn("--skip-smoke", self.sh)
    self.assertGreater(self.sh.rindex("smoke_after_build.sh"), self.sh.index("已部署（设备重启完成）"))

  def test_smoke_failure_fails_publish_but_missing_jungle_only_skips(self):
    self.assertIn("3) echo \"[skip]", self.sh)
    self.assertIn("exit 1", self.sh[self.sh.rindex("smoke_after_build.sh"):])


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
