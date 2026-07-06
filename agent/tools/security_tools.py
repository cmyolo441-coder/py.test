"""Security scanner tools — find secrets, vulnerable patterns, insecure deps.

Real, useful checks that run without any external service:
  * scan_secrets — regex-based API key / password / token detection
  * scan_vulns   — common insecure code patterns (eval, shell=True, etc.)
  * scan_deps    — parse requirements.txt for known-vulnerable package names
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import Tool, ToolResult

_MAX = 10000

# Secret patterns (high-signal, low-false-positive).
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS Secret", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"][A-Za-z0-9/+=]{40}['\"]")),
    ("OpenAI key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Generic API key", re.compile(r"(?i)(api[_-]?key|apikey)\s*[=:]\s*['\"][A-Za-z0-9]{16,}['\"]")),
    ("Password in code", re.compile(r"(?i)password\s*[=:]\s*['\"][^'\"]{4,}['\"]")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
]

# Insecure code patterns.
VULN_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("eval()", "Code injection via eval", re.compile(r"\beval\s*\(")),
    ("exec()", "Code injection via exec", re.compile(r"\bexec\s*\(")),
    ("shell=True", "Shell injection risk", re.compile(r"shell\s*=\s*True")),
    ("os.system", "Shell injection risk", re.compile(r"\bos\.system\s*\(")),
    ("subprocess.call with shell", "Shell injection risk", re.compile(r"subprocess\.(call|run|Popen)\([^)]*shell\s*=\s*True")),
    ("pickle.loads", "Deserialization attack", re.compile(r"pickle\.loads?\s*\(")),
    ("yaml.load (unsafe)", "YAML deserialization", re.compile(r"yaml\.load\s*\((?!\s*Loader)")),
    ("SQL string concat", "SQL injection risk", re.compile(r"(?i)(execute|executemany)\s*\(\s*['\"]?[%f]")),
    ("hardcoded password", "Hardcoded credentials", re.compile(r"(?i)password\s*=\s*['\"][^'\"]{4,}['\"]")),
    ("verify=False", "SSL verification disabled", re.compile(r"verify\s*=\s*False")),
    ("request with no timeout", "Hang risk", re.compile(r"requests\.(get|post|put|delete)\s*\([^)]*\)(?![^)]*timeout)")),
]

# Known-vulnerable packages (a tiny sample — real security uses a CVE DB).
KNOWN_VULN_PACKAGES: dict[str, list[str]] = {
    "requests": ["<2.20.0"],
    "urllib3": ["<1.24.2"],
    "jinja2": ["<2.10.1"],
    "django": ["<2.2.10", "<3.0.2"],
    "flask": ["<0.12.3"],
    "pyyaml": ["<5.1"],
    "cryptography": ["<2.3"],
}


def scan_secrets(path: str) -> ToolResult:
    """Scan a file (or directory) for hardcoded secrets."""
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file() and f.suffix in (".py", ".js", ".ts", ".yaml", ".yml", ".json", ".env", ".toml", ".cfg", ".ini", ".txt")]
    findings: list[str] = []
    for f in files[:200]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{f}:{i}: {name} -> {line.strip()[:120]}")
                    break
    if not findings:
        return ToolResult(output=f"No secrets found in {path}.", metadata={"count": 0})
    return ToolResult(
        output=f"Found {len(findings)} potential secret(s):\n" + "\n".join(findings[:50])[:_MAX],
        metadata={"count": len(findings)},
    )


def scan_vulns(path: str) -> ToolResult:
    """Scan a Python file for insecure code patterns."""
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    files = [p] if p.is_file() else [f for f in p.rglob("*.py") if f.is_file()]
    findings: list[str] = []
    for f in files[:200]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name, desc, pattern in VULN_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{f}:{i}: {name} ({desc}) -> {line.strip()[:120]}")
    if not findings:
        return ToolResult(output=f"No vulnerable patterns found in {path}.", metadata={"count": 0})
    return ToolResult(
        output=f"Found {len(findings)} potential vulnerability(s):\n" + "\n".join(findings[:50])[:_MAX],
        metadata={"count": len(findings)},
    )


def scan_deps(path: str) -> ToolResult:
    """Scan requirements.txt for known-vulnerable package versions."""
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    findings: list[str] = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Parse "package==1.0.0" or "package>=1.0.0" or just "package".
        m = re.match(r"^([A-Za-z0-9_-]+)\s*[=<>!~]+\s*([\d.]+)", line)
        if m:
            pkg, ver = m.group(1).lower(), m.group(2)
            if pkg in KNOWN_VULN_PACKAGES:
                for bad in KNOWN_VULN_PACKAGES[pkg]:
                    if _version_matches(ver, bad):
                        findings.append(f"{pkg}=={ver} is vulnerable (avoid {bad})")
        elif line.lower() in KNOWN_VULN_PACKAGES:
            findings.append(f"{line} — version unconstrained, may be vulnerable")
    if not findings:
        return ToolResult(output=f"No known-vulnerable dependencies in {path}.")
    return ToolResult(
        output=f"Dependency warnings:\n" + "\n".join(findings)[:_MAX],
        metadata={"count": len(findings)},
    )


def _version_matches(version: str, spec: str) -> bool:
    """Cheap version comparison: spec like '<2.20.0'."""
    if spec.startswith("<"):
        return _parse_ver(version) < _parse_ver(spec[1:])
    if spec.startswith(">="):
        return _parse_ver(version) >= _parse_ver(spec[2:])
    return False


def _parse_ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def get_security_tools() -> list[Tool]:
    return [
        Tool(
            name="scan_secrets",
            description="Scan a file or directory for hardcoded API keys, passwords and tokens.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=scan_secrets,
        ),
        Tool(
            name="scan_vulns",
            description="Scan Python code for insecure patterns (eval, shell=True, pickle, etc.).",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=scan_vulns,
        ),
        Tool(
            name="scan_deps",
            description="Scan requirements.txt for known-vulnerable package versions.",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=scan_deps,
        ),
    ]
