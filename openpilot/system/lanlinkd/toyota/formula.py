"""Car Scanner PID 公式求值：先改写成 Python 表达式语法，再按 AST 白名单逐节点求值（不用 eval）。

公式语言（profiles7 里实际出现的写法）：
- 变量：A..Z、AA..FZ 指响应数据字节（取值由调用方给，见 live_data.py）。不分大小写——
  Car Scanner 用的 mXparser 区分大小写，数据里的 `(k*256+L)` 在它那里会失效，这里按 K 处理；
- 四则运算、括号、比较 `= < > <= >= !=`（单个 `=` 是相等）；
- `@<<` / `@>>` 移位；
- 函数：`if(c;a;b)`（c 等于 1 才取 a，否则取 b；只求所走分支）、`GetBit(x,n)`（n 从 0 起，返回 0/1）、`And(a,b)`（按位与）、
  `PID(id)`（另一个 PID 的值）。参数分隔符 `;` 与 `,` 等价。
"""
import ast
import operator
import re
from collections.abc import Callable


class FormulaError(Exception):
  """公式不合法、超出白名单，或求值失败（除零、缺字节、引用的 PID 无值）。"""


_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.LShift: lambda a, b: int(a) << int(b), ast.RShift: lambda a, b: int(a) >> int(b)}
_COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
            ast.Gt: operator.gt, ast.GtE: operator.ge}
_FUNCTION_ARITY = {"if": 3, "getbit": 2, "and": 2, "pid": 1}
_CALL_PREFIX = "fn_"


def _rewrite(formula: str) -> str:
  text = formula.replace("@<<", "<<").replace("@>>", ">>").replace(";", ",")
  text = re.sub(r"(?<![<>!=])=(?!=)", "==", text)
  # 函数名统一加前缀并转小写：避开 Python 关键字 if/and，也让白名单只认这几个名字
  return re.sub(r"\b([A-Za-z_]\w*)\s*\(", lambda m: f"{_CALL_PREFIX}{m.group(1).lower()}(", text)


def evaluate(formula: str, byte_value: Callable[[str], float], pid_value: Callable[[int], float]) -> float:
  try:
    tree = ast.parse(_rewrite(formula), mode="eval")
  except SyntaxError as exc:
    raise FormulaError(f"公式语法错误：{formula}") from exc

  def visit(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
      return visit(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
      return node.value
    if isinstance(node, ast.Name) and not node.id.startswith(_CALL_PREFIX):
      try:
        return byte_value(node.id.upper())
      except (KeyError, IndexError) as exc:
        raise FormulaError(f"响应里没有字节 {node.id}") from exc
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
      value = visit(node.operand)
      return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
      try:
        return _BINARY[type(node.op)](visit(node.left), visit(node.right))
      except ZeroDivisionError as exc:
        raise FormulaError(f"除数为 0：{formula}") from exc
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
      return int(_COMPARE[type(node.ops[0])](visit(node.left), visit(node.comparators[0])))
    if isinstance(node, ast.Call):
      return call(node)
    raise FormulaError(f"公式含不支持的写法：{formula}")

  def call(node: ast.Call) -> float:
    name = node.func.id[len(_CALL_PREFIX):] if isinstance(node.func, ast.Name) and node.func.id.startswith(_CALL_PREFIX) else ""
    if _FUNCTION_ARITY.get(name) != len(node.args) or node.keywords:
      raise FormulaError(f"公式含不支持的函数：{formula}")
    if name == "if":
      return visit(node.args[1]) if visit(node.args[0]) == 1 else visit(node.args[2])
    if name == "pid":
      try:
        return pid_value(int(visit(node.args[0])))
      except KeyError as exc:
        raise FormulaError(f"引用的 PID 没有值：{formula}") from exc
    a, b = (int(visit(arg)) for arg in node.args)
    return (a >> b) & 1 if name == "getbit" else a & b

  return visit(tree)
