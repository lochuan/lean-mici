#!/usr/bin/env python3
"""Tests for the smoke gate's pass/fail logic.

The 2026-09-09 release shipped despite SMOKE: FAIL, so the criteria and their
exit code are worth pinning down.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "smoke_onroad_device.py"
GATE = Path(__file__).resolve().parent / "smoke_gate.sh"


class TestSmokeCriteria(unittest.TestCase):
  """The harness imports cereal at module scope, so inspect the source."""

  @classmethod
  def setUpClass(cls):
    cls.src = HARNESS.read_text()

  def test_selfdrivestate_is_not_a_pass_criterion(self):
    """Fake CAN can't fingerprint, so selfdriveState is always 0 here.

    Gating on it makes the test fail unconditionally, which trains everyone to
    ignore the result -- exactly how the in-car regression slipped through.
    """
    ok_line = next(line for line in self.src.splitlines() if re.match(r"\s*ok = ", line))
    self.assertNotIn("ss_count", ok_line, "selfdriveState must not gate the result")
    self.assertNotIn("max_gap", ok_line, "selfdriveState gap must not gate the result")

  def test_crashes_gate_the_result(self):
    ok_line = next(line for line in self.src.splitlines() if re.match(r"\s*ok = ", line))
    self.assertIn("not crashes", ok_line)

  def test_manager_liveness_gates_the_result(self):
    ok_line = next(line for line in self.src.splitlines() if re.match(r"\s*ok = ", line))
    self.assertIn("saw_manager_state", ok_line)

  def test_nonzero_exit_on_failure(self):
    self.assertIn("return 0 if ok else 1", self.src)

  def test_crash_is_reported_with_exit_code(self):
    """A failure has to name the process and code, or it can't be acted on."""
    self.assertIn("crashes[p.name] = p.exitCode", self.src)
    self.assertRegex(self.src, r'CRASH: \{p\.name\} exitCode=\{p\.exitCode\}')

  def test_does_not_mutate_persistent_car_params(self):
    """Writing CarPlatformBundle would corrupt the real car's fingerprint."""
    self.assertNotIn("CarPlatformBundle", self.src)
    self.assertNotIn("FirmwareQueryDone", self.src)

  def test_limits_are_documented(self):
    self.assertIn("not used as a pass criterion", self.src.replace("NOT", "not"))


class TestSmokeGateScript(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.src = GATE.read_text()

  def test_requires_pass_string(self):
    self.assertIn("SMOKE: PASS", self.src)

  def test_exits_nonzero_on_failure(self):
    self.assertIn("exit 1", self.src)

  def test_checks_both_exit_code_and_output(self):
    self.assertIn('"$rc" -ne 0', self.src)
    self.assertIn("grep -q", self.src)

  def test_expect_commit_gates_the_device_revision(self):
    """Without this the gate silently validates the previous release."""
    self.assertIn("EXPECT_COMMIT", self.src)
    self.assertIn("rev-parse HEAD", self.src)

  def test_shipped_pkls_are_checked(self):
    """A pkl is only readable by the tinygrad that wrote it, and nothing else
    in the pipeline checks it: native_hash only guards release/prebuilt, and
    eagled's YOLO pkl is plain committed source."""
    self.assertIn("check_device_pkls.py", self.src)

  def test_pkl_check_skip_is_explicit(self):
    self.assertIn("SKIP_PKL_CHECK", self.src)


class TestPklCheck(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.src = (Path(__file__).resolve().parent / "check_device_pkls.py").read_text()

  def test_covers_both_shipped_pkls(self):
    self.assertIn("driving_tinygrad.pkl", self.src)
    self.assertIn("yolo_tinygrad.pkl", self.src)

  def test_missing_pkl_is_not_a_silent_pass(self):
    """An empty artifact list must fail, or a wrong --root looks like a PASS."""
    self.assertIn("checked == 0", self.src)

  def test_resolves_chunked_artifacts(self):
    self.assertIn("open_file_chunked", self.src)
    self.assertIn("chunkmanifest", self.src)


if __name__ == "__main__":
  unittest.main()


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
