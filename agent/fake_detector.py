"""Detects fake, placeholder, or simulated code so it can be replaced with
real implementations.

The autonomous engine runs this after each execution pass; any findings are
fed back to the model with an instruction to implement them for real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Signals that strongly suggest non-real / placeholder code.
FAKE_PATTERNS: list[tuple[str, str]] = [
    (r"\bTODO\b", "TODO marker"),
    (r"\bFIXME\b", "FIXME marker"),
    (r"\bplaceholder\b", "placeholder text"),
    (r"\bnot\s+implemented\b", "not implemented"),
    (r"raise\s+NotImplementedError", "NotImplementedError stub"),
    (r"\bpass\s*#.*stub", "stub via pass"),
    (r"\bsimulat(e|ed|ion)\b", "simulation wording"),
    (r"\bfake\b", "fake wording"),
    (r"\bdummy\b", "dummy data"),
    (r"\bmock(ed)?\b(?!ito)", "mock (outside tests)"),
    (r"return\s+['\"]?example['\"]?", "example return value"),
    (r"\.\.\.\s*$", "ellipsis body"),
    (r"#\s*your code here", "'your code here' comment"),
    (r"lorem ipsum", "lorem ipsum filler"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE | re.MULTILINE), label) for p, label in FAKE_PATTERNS]


@dataclass
class Finding:
    line: int
    label: str
    snippet: str


def scan_text(text: str, is_test_file: bool = False) -> list[Finding]:
    """Return findings that indicate fake/placeholder code."""
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), 1):
        for regex, label in _COMPILED:
            # Allow mocks/dummies inside test files.
            if is_test_file and label in ("mock (outside tests)", "dummy data"):
                continue
            if regex.search(line):
                findings.append(Finding(i, label, line.strip()[:120]))
    return findings


def is_real(text: str, is_test_file: bool = False) -> bool:
    return not scan_text(text, is_test_file)


def report(text: str, is_test_file: bool = False) -> str:
    findings = scan_text(text, is_test_file)
    if not findings:
        return "REAL: no placeholder/simulated code detected."
    lines = [f"FAKE CODE DETECTED ({len(findings)} issue(s)):"]
    for f in findings:
        lines.append(f"  line {f.line}: {f.label} -> {f.snippet}")
    return "\n".join(lines)
