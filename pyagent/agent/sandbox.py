"""Restricted Python code execution sandbox.

Runs short snippets in a subprocess with a timeout and a restricted builtin
set. This is a pragmatic sandbox for a local dev tool, not a hardened security
boundary; do not expose it to untrusted remote input.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

_PREAMBLE = textwrap.dedent(
    """
    import builtins
    _BLOCKED = {
        'open', 'exec', 'eval', 'compile', '__import__', 'input',
    }
    _orig_import = builtins.__import__
    _ALLOWED_MODULES = {
        'math', 'statistics', 'random', 'itertools', 'functools',
        'collections', 'datetime', 're', 'json', 'string', 'decimal',
    }
    def _guard_import(name, *args, **kwargs):
        root = name.split('.')[0]
        if root not in _ALLOWED_MODULES:
            raise ImportError(f"import of '{name}' is not allowed in sandbox")
        return _orig_import(name, *args, **kwargs)
    builtins.__import__ = _guard_import
    for _name in _BLOCKED:
        if hasattr(builtins, _name):
            setattr(builtins, _name, None)
    """
)


def run_python(code: str, timeout: int = 10) -> str:
    """Execute ``code`` in a restricted subprocess and return combined output."""
    if not code.strip():
        return "Error: empty code"
    script = _PREAMBLE + "\n" + code
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(script)
        temp_path = fh.name
    try:
        result = subprocess.run(
            [sys.executable, "-I", temp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: execution timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return f"Error: {exc}"
    finally:
        try:
            Path(temp_path).unlink()
        except OSError:
            pass

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if err:
        out = (out + "\n[stderr]\n" + err) if out else ("[stderr]\n" + err)
    return out or f"(exit code {result.returncode}, no output)"


def register() -> list[tuple[dict[str, Any], Callable[..., str]]]:
    schema = {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "Execute a short Python snippet in a restricted sandbox.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    }
    return [(schema, run_python)]
