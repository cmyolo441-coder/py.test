"""Python code execution tool.

Runs a snippet of Python in a subprocess (isolated interpreter) with a timeout
so it cannot block the agent. Captures stdout/stderr and the exit code.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Tool, ToolResult

_MAX = 10000


def run_python(code: str, timeout: int = 700000) -> ToolResult:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
        fh.write(code)
        script = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(output=f"Execution timed out after {timeout}s", success=False)
    finally:
        Path(script).unlink(missing_ok=True)

    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.strip() or "(no output)"
    return ToolResult(
        output=output[:_MAX],
        success=proc.returncode == 0,
        metadata={"returncode": proc.returncode},
    )


def get_python_tools() -> list[Tool]:
    return [
        Tool(
            name="run_python",
            description=(
                "Execute a Python 3 code snippet in an isolated subprocess and "
                "return its stdout/stderr. Use for calculations, data processing "
                "or quick scripts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to run."},
                    "timeout": {"type": "integer", "default": 700000},
                },
                "required": ["code"],
            },
            func=run_python,
            dangerous=True,
        )
    ]
