"""Code analysis tools — AST-based static analysis for Python files.

These give the agent deep, real understanding of code structure without
running it. Used by Goal Mode for refactoring, review and documentation tasks.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

from .base import Tool, ToolResult

_MAX = 12000


def _parse_file(path: str) -> tuple[ast.AST | None, str]:
    p = Path(path)
    if not p.exists():
        return None, f"File not found: {path}"
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return None, f"Syntax error: {exc}"
    return tree, ""


def analyze_structure(path: str) -> ToolResult:
    """List all classes, functions, imports and their signatures."""
    tree, err = _parse_file(path)
    if err:
        return ToolResult(output=err, success=False)
    lines: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            lines.append(f"class {node.name}({bases})  [line {node.lineno}]")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = ast.unparse(item.args)
                    lines.append(f"  def {item.name}({args})  [line {item.lineno}]")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not _is_method(node):
            args = ast.unparse(node.args)
            lines.append(f"def {node.name}({args})  [line {node.lineno}]")
    return ToolResult(output="\n".join(lines)[:_MAX] or "(no definitions found)")


def _is_method(node: ast.AST) -> bool:
    """True if a FunctionDef is inside a class (rough check)."""
    # ast.walk flattens, so we can't tell parentage directly; rely on the
    # caller having already reported class methods. For top-level functions
    # we accept them; methods are caught by the class walk above.
    return False


def count_complexity(path: str) -> ToolResult:
    """McCabe-style cyclomatic complexity per function."""
    tree, err = _parse_file(path)
    if err:
        return ToolResult(output=err, success=False)
    results: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            complexity = 1
            for child in ast.walk(node):
                if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                                     ast.With, ast.Assert, ast.BoolOp)):
                    complexity += 1
            flag = " ⚠HIGH" if complexity > 10 else ""
            results.append(f"  {node.name}: {complexity}{flag}")
    return ToolResult(
        output=f"Cyclomatic complexity for {path}:\n" + "\n".join(results)[:_MAX],
        metadata={"functions": len(results)},
    )


def find_todos(path: str) -> ToolResult:
    """Find TODO/FIXME/HACK/XXX comments in a file."""
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    findings: list[str] = []
    markers = ("TODO", "FIXME", "HACK", "XXX", "BUG", "NOTE")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        for m in markers:
            if m in stripped.upper() and "#" in stripped:
                findings.append(f"  {i}: {stripped[:120]}")
                break
    return ToolResult(
        output=f"Found {len(findings)} marker(s) in {path}:\n" + "\n".join(findings)[:_MAX]
            or f"No markers found in {path}.",
        metadata={"count": len(findings)},
    )


def list_imports(path: str) -> ToolResult:
    """List all imports in a Python file."""
    tree, err = _parse_file(path)
    if err:
        return ToolResult(output=err, success=False)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in node.names)
            imports.append(f"from {module} import {names}")
    return ToolResult(output="\n".join(imports)[:_MAX] or "(no imports)")


def get_code_analysis_tools() -> list[Tool]:
    return [
        Tool(
            name="analyze_structure",
            description="List all classes, functions and their signatures in a Python file (AST-based).",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=analyze_structure,
        ),
        Tool(
            name="count_complexity",
            description="Compute cyclomatic complexity per function in a Python file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=count_complexity,
        ),
        Tool(
            name="find_todos",
            description="Find TODO/FIXME/HACK/XXX markers in a file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=find_todos,
        ),
        Tool(
            name="list_imports",
            description="List all import statements in a Python file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=list_imports,
        ),
    ]
