"""Powerful, guarded shell executor.

Unlike the conservative allow-list in tools.py, this executor can run *any*
command, but with real enterprise safety controls:
  - dangerous-command detection (blocked unless explicitly allowed)
  - approval callback for risky operations
  - configurable working directory and timeout
  - full audit logging of every command and its result
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Patterns that indicate potentially destructive operations.
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\b",
    r"\bmkfs\b",
    r"\bdd\b",
    r":\(\)\s*\{",              # fork bomb
    r">\s*/dev/sd",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bchmod\s+-R\s+777\b",
    r"\bcurl\b.*\|\s*(sh|bash)",
    r"\bwget\b.*\|\s*(sh|bash)",
]

_DANGER_RE = re.compile("|".join(DANGEROUS_PATTERNS))


@dataclass
class ShellResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration: float
    blocked: bool = False

    def as_text(self) -> str:
        if self.blocked:
            return f"BLOCKED: '{self.command}' was rejected by safety policy."
        out = self.stdout.strip()
        if self.stderr.strip():
            out += ("\n[stderr]\n" + self.stderr.strip()) if out else self.stderr.strip()
        return out or f"(exit {self.returncode}, {self.duration:.2f}s, no output)"


@dataclass
class ShellExecutor:
    """Runs shell commands with guardrails and auditing."""

    workdir: str = "."
    default_timeout: int = 700000
    allow_dangerous: bool = False
    approval_callback: Callable[[str], bool] | None = None
    audit: list[ShellResult] = field(default_factory=list)

    def is_dangerous(self, command: str) -> bool:
        return bool(_DANGER_RE.search(command))

    def run(self, command: str, timeout: int | None = None) -> ShellResult:
        if not command.strip():
            return ShellResult(command, 1, "", "empty command", 0.0, blocked=True)

        if self.is_dangerous(command):
            approved = self.allow_dangerous
            if not approved and self.approval_callback is not None:
                approved = self.approval_callback(command)
            if not approved:
                result = ShellResult(command, 126, "", "dangerous command", 0.0, blocked=True)
                self.audit.append(result)
                return result

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
            )
            result = ShellResult(
                command, proc.returncode, proc.stdout, proc.stderr,
                time.perf_counter() - start,
            )
        except subprocess.TimeoutExpired:
            result = ShellResult(
                command, 124, "", f"timed out after {timeout or self.default_timeout}s",
                time.perf_counter() - start,
            )
        except Exception as exc:  # noqa: BLE001
            result = ShellResult(command, 1, "", str(exc), time.perf_counter() - start)

        self.audit.append(result)
        return result


# A module-level default executor used by the tool wrapper.
default_executor = ShellExecutor()


def shell_exec(command: str, timeout: int = 700000) -> str:
    return default_executor.run(command, timeout=timeout).as_text()


def register() -> list[tuple[dict[str, Any], Callable[..., str]]]:
    schema = {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": (
                "Run any shell command with safety guardrails. Destructive "
                "commands are blocked unless explicitly approved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds. Default 700000."},
                },
                "required": ["command"],
            },
        },
    }
    return [(schema, shell_exec)]
