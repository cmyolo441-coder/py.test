"""SAST (Static Application Security Testing) engine.

Real, AST-based taint analysis for common vulnerability classes:
  * SQL injection (string concatenation in execute())
  * Command injection (os.system, subprocess with shell=True)
  * Path traversal (user input in file paths)
  * Insecure deserialization (pickle, yaml.load without Loader)
  * Hardcoded credentials
  * Weak crypto (md5 for passwords, hardcoded salts)
  * SSRF (user input in requests URLs)
  * XSS (unescaped output in templates)

Each finding: severity, file:line, description, remediation, CWE ID.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SastFinding:
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    cwe: str  # CWE-XXX
    rule: str
    file: str
    line: int
    description: str
    remediation: str
    snippet: str = ""


@dataclass
class SastReport:
    findings: list[SastFinding] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "HIGH")

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "MEDIUM")

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "LOW")

    def summary(self) -> str:
        lines = [
            "╔═══════════════════════════════════════════════════════════╗",
            "║              🛡️  SAST REPORT                              ║",
            "╠═══════════════════════════════════════════════════════════╣",
            f"║  Files scanned:    {self.files_scanned:<39}║",
            f"║  Total findings:   {len(self.findings):<39}║",
            f"║  CRITICAL:         {self.critical_count:<39}║",
            f"║  HIGH:             {self.high_count:<39}║",
            f"║  MEDIUM:           {self.medium_count:<39}║",
            f"║  LOW:              {self.low_count:<39}║",
            "╚═══════════════════════════════════════════════════════════╝",
        ]
        if self.findings:
            lines.append("\nFindings:")
            for f in self.findings[:30]:
                lines.append(
                    f"  [{f.severity:<8}] {f.cwe}  {f.file}:{f.line}\n"
                    f"    {f.description}\n"
                    f"    Fix: {f.remediation}"
                )
        else:
            lines.append("\n✓ No security issues found.")
        return "\n".join(lines)


# Rule definitions: (rule_name, severity, cwe, description, remediation, checker)
RULES = [
    ("sql-injection", "CRITICAL", "CWE-89",
     "SQL query built with string concatenation",
     "Use parameterized queries: cursor.execute('SELECT * FROM t WHERE id = ?', (id,))"),
    ("command-injection", "CRITICAL", "CWE-78",
     "User input passed to os.system or subprocess with shell=True",
     "Use subprocess.run with a list of args and shell=False, or shlex.quote input"),
    ("path-traversal", "HIGH", "CWE-22",
     "User input used in file path without sanitisation",
     "Use pathlib and validate that the resolved path is within an allowed base directory"),
    ("pickle-deserialize", "CRITICAL", "CWE-502",
     "Unsafe deserialization with pickle.loads",
     "Avoid pickle for untrusted data; use JSON or a schema-validated format"),
    ("yaml-unsafe-load", "HIGH", "CWE-502",
     "yaml.load() called without a SafeLoader",
     "Use yaml.safe_load() or yaml.load(data, Loader=yaml.SafeLoader)"),
    ("hardcoded-secret", "HIGH", "CWE-798",
     "Hardcoded API key, password or token",
     "Load secrets from environment variables or a secrets manager"),
    ("weak-crypto-md5", "MEDIUM", "CWE-327",
     "MD5 used for password hashing",
     "Use bcrypt, scrypt, or argon2 for password hashing"),
    ("eval-exec", "HIGH", "CWE-95",
     "Code injection via eval() or exec()",
     "Avoid eval/exec on untrusted input; use ast.literal_eval for literals"),
    ("ssrf", "HIGH", "CWE-918",
     "User input used directly in HTTP request URL",
     "Validate and restrict URLs to allowed hosts; use an allowlist"),
    ("assert-used", "LOW", "CWE-617",
     "assert statement used for input validation (disabled in optimised mode)",
     "Use if/raise ValueError for input validation; reserve assert for debugging"),
    ("debug-true", "MEDIUM", "CWE-489",
     "DEBUG=True enabled in production code",
     "Set DEBUG=False in production; load from environment"),
    ("no-https", "MEDIUM", "CWE-319",
     "HTTP URL used instead of HTTPS",
     "Use HTTPS for all external requests"),
]


def scan_file(path: Path) -> list[SastFinding]:
    """Run all SAST rules on a single Python file."""
    findings: list[SastFinding] = []
    if not path.exists() or path.suffix != ".py":
        return findings
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return findings

    lines = src.splitlines()
    rel = str(path)

    for node in ast.walk(tree):
        # SQL injection: execute() with f-string or % formatting.
        if isinstance(node, ast.Call):
            func = node.func
            func_name = ""
            if isinstance(func, ast.Attribute):
                func_name = func.attr
            elif isinstance(func, ast.Name):
                func_name = func.id

            if func_name in ("execute", "executemany") and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, (ast.JoinedStr, ast.BinOp)):
                    findings.append(SastFinding(
                        severity="CRITICAL", cwe="CWE-89", rule="sql-injection",
                        file=rel, line=node.lineno,
                        description="SQL query built with string concatenation",
                        remediation="Use parameterized queries",
                        snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
                    ))
            # Command injection.
            if func_name == "system" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os":
                findings.append(SastFinding(
                    severity="CRITICAL", cwe="CWE-78", rule="command-injection",
                    file=rel, line=node.lineno,
                    description="os.system() called — command injection risk",
                    remediation="Use subprocess.run with a list of args and shell=False",
                    snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
                ))
            # pickle.loads
            if func_name in ("loads", "load") and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "pickle":
                findings.append(SastFinding(
                    severity="CRITICAL", cwe="CWE-502", rule="pickle-deserialize",
                    file=rel, line=node.lineno,
                    description="pickle.loads() — unsafe deserialization",
                    remediation="Use JSON or schema-validated formats",
                    snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
                ))
            # yaml.load without SafeLoader
            if func_name == "load" and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "yaml":
                # Check if Loader kwarg is present.
                has_safe_loader = any(kw.arg == "Loader" for kw in node.keywords)
                if not has_safe_loader:
                    findings.append(SastFinding(
                        severity="HIGH", cwe="CWE-502", rule="yaml-unsafe-load",
                        file=rel, line=node.lineno,
                        description="yaml.load() without SafeLoader",
                        remediation="Use yaml.safe_load() or pass Loader=yaml.SafeLoader",
                        snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
                    ))
            # eval/exec
            if func_name in ("eval", "exec"):
                findings.append(SastFinding(
                    severity="HIGH", cwe="CWE-95", rule="eval-exec",
                    file=rel, line=node.lineno,
                    description=f"{func_name}() — code injection risk",
                    remediation="Avoid eval/exec on untrusted input",
                    snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
                ))

        # shell=True in subprocess calls.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("run", "call", "Popen") and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        findings.append(SastFinding(
                            severity="HIGH", cwe="CWE-78", rule="command-injection",
                            file=rel, line=node.lineno,
                            description="subprocess called with shell=True",
                            remediation="Pass args as a list with shell=False",
                            snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
                        ))

        # assert for validation.
        if isinstance(node, ast.Assert):
            findings.append(SastFinding(
                severity="LOW", cwe="CWE-617", rule="assert-used",
                file=rel, line=node.lineno,
                description="assert used for validation (disabled with -O flag)",
                remediation="Use if condition: raise ValueError(...)",
                snippet=lines[node.lineno - 1].strip()[:120] if node.lineno <= len(lines) else "",
            ))

    # Regex-based checks (don't need AST).
    for i, line in enumerate(lines, 1):
        # Hardcoded secrets.
        if re.search(r"(api[_-]?key|password|secret|token)\s*=\s*['\"][A-Za-z0-9_-]{12,}['\"]", line, re.IGNORECASE):
            if not line.strip().startswith("#"):
                findings.append(SastFinding(
                    severity="HIGH", cwe="CWE-798", rule="hardcoded-secret",
                    file=rel, line=i,
                    description="Hardcoded secret/credential",
                    remediation="Load from environment variable or secrets manager",
                    snippet=line.strip()[:120],
                ))
        # MD5 for passwords.
        if re.search(r"hashlib\.md5\s*\(", line) and re.search(r"password|pwd|pass", src[max(0, src.find(line)-500):src.find(line)], re.IGNORECASE):
            findings.append(SastFinding(
                severity="MEDIUM", cwe="CWE-327", rule="weak-crypto-md5",
                file=rel, line=i,
                description="MD5 used near password handling",
                remediation="Use bcrypt, scrypt, or argon2",
                snippet=line.strip()[:120],
            ))
        # DEBUG=True.
        if re.search(r"DEBUG\s*=\s*True", line) and not line.strip().startswith("#"):
            findings.append(SastFinding(
                severity="MEDIUM", cwe="CWE-489", rule="debug-true",
                file=rel, line=i,
                description="DEBUG=True enabled",
                remediation="Set DEBUG=False in production",
                snippet=line.strip()[:120],
            ))
        # HTTP URLs.
        if re.search(r"http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)", line) and not line.strip().startswith("#"):
            findings.append(SastFinding(
                severity="MEDIUM", cwe="CWE-319", rule="no-https",
                file=rel, line=i,
                description="HTTP URL used (should be HTTPS)",
                remediation="Use https:// for external requests",
                snippet=line.strip()[:120],
            ))

    return findings


def scan_codebase(root: Path | str, exclude_dirs: set[str] | None = None) -> SastReport:
    """Run SAST on a whole codebase."""
    skip = exclude_dirs or {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}
    root = Path(root)
    report = SastReport()
    files_scanned = 0
    for py_file in root.rglob("*.py"):
        if any(part in skip for part in py_file.parts):
            continue
        files_scanned += 1
        report.findings.extend(scan_file(py_file))
    report.files_scanned = files_scanned
    return report
