"""Example plugin: a safe arithmetic calculator tool.

Demonstrates the plugin contract. Copy this file to build your own tools.
"""
from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("Unsupported or unsafe expression")


def calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        return str(_eval(tree))
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"


def register() -> list[tuple[dict[str, Any], Callable[..., str]]]:
    schema = {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Safely evaluate a basic arithmetic expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "e.g. '2 * (3 + 4) ** 2'",
                    }
                },
                "required": ["expression"],
            },
        },
    }
    return [(schema, calculate)]
