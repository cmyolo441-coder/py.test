"""Security helpers: command risk assessment and secret redaction."""

from __future__ import annotations

import re

# Patterns that indicate a potentially destructive shell command.
_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf?\s+/",
    r"\brm\s+-rf?\s+\*",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{.*\};:",       # fork bomb
    r"\bformat\b\s+[a-z]:",    # windows format
    r"Remove-Item.*-Recurse.*-Force.*[\\/]$",
    r">\s*/dev/sd[a-z]",
    r"\bchmod\s+-R\s+777\s+/",
    r"\bshutdown\b",
    r"\breboot\b",
]

_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk-***"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([A-Za-z0-9\-_]{12,})"), r"\1***"),
    (re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-_.]{12,})"), r"\1***"),
    (re.compile(r"(?i)(password\s*[=:]\s*)(\S+)"), r"\1***"),
]

_compiled_danger = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_PATTERNS]


def assess_command(command: str) -> tuple[bool, str | None]:
    """Return (is_dangerous, reason)."""
    for pat in _compiled_danger:
        if pat.search(command):
            return True, f"matches dangerous pattern: {pat.pattern}"
    return False, None


def redact_secrets(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text
