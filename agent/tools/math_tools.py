"""Math & datetime tools: safe expression evaluation and time helpers."""

from __future__ import annotations

import ast
import datetime
import math
import operator as op

from .base import Tool, ToolResult

# Whitelisted operators for safe arithmetic evaluation.
_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.Pow: op.pow, ast.Mod: op.mod, ast.FloorDiv: op.floordiv,
    ast.USub: op.neg, ast.UAdd: op.pos,
}
_FUNCS = {k: getattr(math, k) for k in (
    "sqrt", "sin", "cos", "tan", "log", "log2", "log10", "exp", "floor", "ceil", "fabs"
)}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("only numeric constants allowed")
    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"unknown name: {node.id}")
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
        raise ValueError("unknown function")
    raise ValueError("unsupported expression")


def calculate(expression: str) -> ToolResult:
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree.body)
    except (SyntaxError, ValueError, ZeroDivisionError, KeyError) as exc:
        return ToolResult(output=f"Error: {exc}", success=False)
    return ToolResult(output=str(result))


def now(timezone_offset: int = 0) -> ToolResult:
    tz = datetime.timezone(datetime.timedelta(hours=timezone_offset))
    dt = datetime.datetime.now(tz)
    return ToolResult(output=dt.isoformat())


def date_diff(start: str, end: str) -> ToolResult:
    try:
        d1 = datetime.date.fromisoformat(start)
        d2 = datetime.date.fromisoformat(end)
    except ValueError as exc:
        return ToolResult(output=f"Invalid date (use YYYY-MM-DD): {exc}", success=False)
    return ToolResult(output=f"{abs((d2 - d1).days)} days")


def get_math_tools() -> list[Tool]:
    return [
        Tool("calculate", "Safely evaluate a math expression (supports sqrt, sin, log, pi, e, etc.).",
             {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}, calculate),
        Tool("now", "Get the current date/time in ISO format for a UTC offset.",
             {"type": "object", "properties": {"timezone_offset": {"type": "integer", "default": 0}}}, now),
        Tool("date_diff", "Compute the number of days between two YYYY-MM-DD dates.",
             {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]}, date_diff),
    ]
