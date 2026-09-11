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


if __name__ == "__main__":
  unittest.main()
