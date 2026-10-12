"""Line-by-line calculator for math notes (first line `math`)."""
import ast
import gettext
import math
import operator
import re
from typing import Any, Callable

_ = gettext.gettext

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor,
    "ceil": math.ceil, "log": math.log, "ln": math.log, "log10": math.log10,
    "min": min, "max": max, "sin": math.sin, "cos": math.cos, "tan": math.tan,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_AGGREGATES = {"sum", "total", "avg", "average"}

_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_]\w*)\s*=\s*(.+)$')
_PERCENT_OF_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*of\b', re.IGNORECASE)
_THOUSANDS_RE = re.compile(r'(?<=\d),(?=\d{3}(?!\d))')


def _eval_node(node, names):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(node.id)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        # Guard against 9**9**9 freezing the UI.
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _BIN_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand, names))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*(_eval_node(a, names) for a in node.args))
    raise ValueError("unsupported")


def evaluate(expr, names):
    """Evaluate one expression; returns a number or None."""
    expr = expr.strip().replace('^', '**').replace('×', '*').replace('÷', '/')
    expr = _THOUSANDS_RE.sub('', expr)
    expr = re.sub(r'[$€£¥]', '', expr)
    expr = _PERCENT_OF_RE.sub(r'(\1/100)*', expr)
    if not expr:
        return None
    try:
        value = _eval_node(ast.parse(expr, mode='eval'), names)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def format_number(value):
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.10g}"


class MathError(str):
    """A result string that should be displayed as an error."""


def evaluate_lines(lines):
    """Return one result string (or None) per line.

    Supports `name = expr` variables, `ans` (previous result) and
    `sum`/`total`/`avg`/`average` over the results since the last blank line.
    Redefining a variable yields a MathError and keeps the first value.
    """
    names = {}
    defined = set()
    block = []
    results = []
    for line in lines:
        stripped = line.strip()
        result = None
        if not stripped:
            block = []
        elif not stripped.startswith('//'):
            key = stripped.lower().rstrip(':')
            if key in _AGGREGATES:
                if block:
                    result = sum(block) if key in ("sum", "total") else sum(block) / len(block)
                block = []
            else:
                m = _ASSIGN_RE.match(stripped)
                if m and m.group(1) in defined:
                    results.append(MathError(_("already defined")))
                    continue
                result = evaluate(m.group(2) if m else stripped, names)
                if result is not None:
                    if m:
                        names[m.group(1)] = result
                        defined.add(m.group(1))
                    block.append(result)
            if result is not None:
                names["ans"] = result
        # Echoing a bare number back adds nothing.
        if result is not None and re.fullmatch(r'[\d.,\s]+', stripped):
            result = None
        results.append(None if result is None else format_number(result))
    return results

