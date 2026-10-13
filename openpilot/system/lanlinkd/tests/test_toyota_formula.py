import pytest

from openpilot.system.lanlinkd.toyota.formula import FormulaError, evaluate

BYTES = {"A": 0x81, "B": 2, "C": 3, "D": 4, "K": 5, "L": 6, "AA": 0x12, "AB": 0x34}


def ev(formula, pids=None):
  return evaluate(formula, lambda name: BYTES[name], lambda pid: (pids or {})[pid])


class TestArithmetic:
  @pytest.mark.parametrize("formula,value", [
    ("A*36.36/255-18.18", 0x81 * 36.36 / 255 - 18.18),
    ("(B*256*256+C*256+D)*1.609", (2 * 65536 + 3 * 256 + 4) * 1.609),
    ("(AA*256+AB)/1000", 0x1234 / 1000),
    ("-B+C", 1),
    ("(k*256+L)*5/512", (5 * 256 + 6) * 5 / 512),   # 变量名不分大小写
  ])
  def test_values(self, formula, value):
    assert ev(formula) == pytest.approx(value)


class TestFunctions:
  def test_if_with_semicolons_and_single_equals(self):
    assert ev("if(B=2;1;0)") == 1
    assert ev("if(B=3;1;0)") == 0
    assert ev("if(A=80;0;(A*49.8/255)-25)") == pytest.approx(0x81 * 49.8 / 255 - 25)

  def test_if_condition_must_equal_one(self):
    assert ev("if(B;1;0)") == 0   # B=2：非零但不等于 1，Car Scanner 走假分支

  def test_if_only_evaluates_taken_branch(self):
    assert ev("if(B=2;1;1/0)") == 1

  def test_comparisons(self):
    assert ev("if(A>B;1;0)") == 1 and ev("if(A<B;1;0)") == 0

  def test_getbit_is_zero_based(self):
    assert ev("GetBit(A,7)") == 1 and ev("GetBit(A,0)") == 1 and ev("GetBit(A,1)") == 0
    assert ev("(A-GetBit(A,7)*256)*199.27/127") == pytest.approx((0x81 - 256) * 199.27 / 127)

  def test_and_is_bitwise(self):
    assert ev("And(A,7)") == 1
    assert ev("if(And(A;1)=1;B;C)") == 2

  def test_pid_reference(self):
    assert ev("(PID(825)-PID(824))*1000", {825: 3.9, 824: 3.85}) == pytest.approx(50)


class TestRejects:
  @pytest.mark.parametrize("formula", [
    "__import__('os')", "A.__class__", "open(A)", "[A]", "lambda: 1", "A if B else C", "'x'", "A**B",
  ])
  def test_anything_outside_whitelist(self, formula):
    with pytest.raises(FormulaError):
      ev(formula)

  def test_division_by_zero_is_formula_error(self):
    with pytest.raises(FormulaError):
      ev("A/(B-2)")

  def test_unknown_byte_is_formula_error(self):
    def missing(name):
      raise KeyError(name)
    with pytest.raises(FormulaError):
      evaluate("Z*2", missing, lambda pid: 0)
