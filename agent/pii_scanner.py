"""PII (Personally Identifiable Information) scanner.

Detects sensitive data in text files:
  * Email addresses
  * Phone numbers (US/India formats)
  * Credit card numbers (with Luhn check)
  * SSN (US Social Security Numbers)
  * Aadhaar numbers (India)
  * IP addresses
  * PAN cards (India)
  * Passport numbers

Returns findings with file, line, type and a masked preview.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PiiFinding:
    file: str
    line: int
    pii_type: str
    snippet: str  # masked
    confidence: float = 1.0


@dataclass
class PiiReport:
    findings: list[PiiFinding] = field(default_factory=list)
    files_scanned: int = 0

    def summary(self) -> str:
        lines = [
            f"PII scan ({self.files_scanned} files, {len(self.findings)} findings):",
        ]
        if not self.findings:
            lines.append("  ✓ No PII found.")
            return "\n".join(lines)
        by_type: dict[str, int] = {}
        for f in self.findings:
            by_type[f.pii_type] = by_type.get(f.pii_type, 0) + 1
        lines.append("  by type:")
        for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
            lines.append(f"    {t:<20} {c}")
        lines.append("\n  Findings:")
        for f in self.findings[:30]:
            lines.append(f"    {f.file}:{f.line}  [{f.pii_type}]  {f.snippet}")
        return "\n".join(lines)


# PII patterns. Each: (type, compiled regex, mask function).
_PATTERNS: list[tuple[str, re.Pattern, Any]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), lambda m: m.group(0)[:2] + "***@" + m.group(0).split("@")[1]),
    ("phone_us", re.compile(r"\b\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), lambda m: m.group(0)[:3] + "-XXX-XXXX"),
    ("phone_in", re.compile(r"\b\+?91[-.\s]?\d{10}\b"), lambda m: "+91-XXXXXXXXXX"),
    ("ssn_us", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), lambda m: "XXX-XX-XXXX"),
    ("aadhaar_in", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), lambda m: "XXXX XXXX XXXX"),
    ("pan_in", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), lambda m: "XXXXX0000X"),
    ("passport_us", re.compile(r"\b[A-Z]\d{8}\b"), lambda m: "X00000000"),
    ("ip_address", re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), lambda m: m.group(0).split(".")[0] + ".x.x.x"),
]

# Credit card pattern + Luhn check.
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _luhn_check(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def scan_text_for_pii(text: str, file_path: str = "") -> list[PiiFinding]:
    findings: list[PiiFinding] = []
    for i, line in enumerate(text.splitlines(), 1):
        # Credit cards (with Luhn).
        for m in _CC_RE.finditer(line):
            if _luhn_check(m.group(0)):
                masked = m.group(0)[:4] + " **** **** " + m.group(0)[-4:]
                findings.append(PiiFinding(file=file_path, line=i, pii_type="credit_card", snippet=masked))
        # Other patterns.
        for pii_type, pattern, masker in _PATTERNS:
            for m in pattern.finditer(line):
                findings.append(PiiFinding(
                    file=file_path, line=i, pii_type=pii_type, snippet=masker(m),
                ))
    return findings


def scan_file_for_pii(path: Path) -> list[PiiFinding]:
    if not path.exists() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_text_for_pii(text, str(path))


def scan_directory_for_pii(root: Path | str, exclude_dirs: set[str] | None = None) -> PiiReport:
    skip = exclude_dirs or {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}
    root = Path(root)
    report = PiiReport()
    valid_exts = {".py", ".js", ".ts", ".json", ".yaml", ".yml", ".txt", ".md", ".csv", ".env", ".cfg", ".ini", ".log"}
    for f in root.rglob("*"):
        if any(part in skip for part in f.parts):
            continue
        if not f.is_file() or f.suffix.lower() not in valid_exts:
            continue
        report.findings.extend(scan_file_for_pii(f))
        report.files_scanned += 1
    return report
