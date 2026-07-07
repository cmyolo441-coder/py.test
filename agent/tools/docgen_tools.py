"""Documentation and test generation tools — extract docstrings, generate stubs.

Real, AST-based tools that:
  * extract_docstrings — pull all docstrings from a Python file
  * generate_doc_stubs — insert placeholder docstrings where missing
  * count_test_coverage — count test functions vs source functions
  * list_test_functions — list all test_* functions in a test file
"""
from __future__ import annotations

import ast
from pathlib import Path

from .base import Tool, ToolResult

_MAX = 128000


def extract_docstrings(path: str) -> ToolResult:
    """Extract all module/class/function docstrings from a Python file."""
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return ToolResult(output=f"Syntax error: {exc}", success=False)
    lines: list[str] = []
    # Module docstring.
    if ast.get_docstring(tree):
        lines.append(f"[module] {ast.get_docstring(tree)[:200]}")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node)
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            if doc:
                lines.append(f"[{kind}] {node.name} (line {node.lineno}): {doc[:200]}")
            else:
                lines.append(f"[{kind}] {node.name} (line {node.lineno}): (no docstring)")
    return ToolResult(output="\n".join(lines)[:_MAX] or "(no docstrings found)")


def generate_doc_stubs(path: str) -> ToolResult:
    """Insert placeholder docstrings for functions/classes missing them.

    Reads the file, rewrites it in-place with stubs added. Returns a summary.
    """
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    src = p.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return ToolResult(output=f"Syntax error: {exc}", success=False)
    # Collect (line_number, name, kind) for undocumented defs.
    missing: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not ast.get_docstring(node):
                kind = "Class" if isinstance(node, ast.ClassDef) else "Function"
                missing.append((node.lineno, node.name, kind))
    if not missing:
        return ToolResult(output=f"All functions/classes in {path} already have docstrings.")
    # We can't easily rewrite in-place with AST without losing formatting,
    # so we report what's missing and let the model do the actual insertion.
    report = "\n".join(f"  line {ln}: {kind} '{name}'" for ln, name, kind in missing)
    return ToolResult(
        output=f"{len(missing)} missing docstring(s) in {path}:\n{report}\n\n"
               f"Use fs_insert_at_line to add docstrings after each def line.",
        metadata={"count": len(missing)},
    )


def count_test_coverage(source_path: str, test_path: str) -> ToolResult:
    """Count source functions vs test functions (rough coverage signal)."""
    src = Path(source_path)
    tst = Path(test_path)
    if not src.exists() or not tst.exists():
        return ToolResult(output="Both source and test files must exist.", success=False)
    try:
        src_tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
        tst_tree = ast.parse(tst.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return ToolResult(output=f"Syntax error: {exc}", success=False)
    src_funcs = sum(1 for n in ast.walk(src_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    test_funcs = sum(1 for n in ast.walk(tst_tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"))
    ratio = (test_funcs / src_funcs * 100) if src_funcs else 0
    return ToolResult(
        output=f"Source: {src_funcs} functions\nTests: {test_funcs} test functions\nRatio: {ratio:.0f}%",
        metadata={"source_functions": src_funcs, "test_functions": test_funcs, "ratio_pct": ratio},
    )


def list_test_functions(path: str) -> ToolResult:
    """List all test_* functions in a Python test file."""
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return ToolResult(output=f"Syntax error: {exc}", success=False)
    tests: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            args = ast.unparse(node.args)
            tests.append(f"  {node.name}({args})  [line {node.lineno}]")
    return ToolResult(
        output=f"{len(tests)} test(s) in {path}:\n" + "\n".join(tests)[:_MAX] or "No tests found.",
        metadata={"count": len(tests)},
    )


def get_docgen_tools() -> list[Tool]:
    return [
        Tool(
            name="extract_docstrings",
            description="Extract all module/class/function docstrings from a Python file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=extract_docstrings,
        ),
        Tool(
            name="generate_doc_stubs",
            description="Find functions/classes missing docstrings and report them for insertion.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=generate_doc_stubs,
        ),
        Tool(
            name="count_test_coverage",
            description="Count source functions vs test functions for a rough coverage signal.",
            parameters={
                "type": "object",
                "properties": {"source_path": {"type": "string"}, "test_path": {"type": "string"}},
                "required": ["source_path", "test_path"],
            },
            func=count_test_coverage,
        ),
        Tool(
            name="list_test_functions",
            description="List all test_* functions in a Python test file.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=list_test_functions,
        ),
    ]
