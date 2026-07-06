"""Container & IaC scanners — Dockerfile, Terraform, CloudFormation analysis.

Real pattern-based scanners that find insecure configurations:
  * Dockerfile: running as root, no HEALTHCHECK, secrets in ENV, :latest tag
  * Terraform:  public S3 buckets, 0.0.0.0/0 ingress, hardcoded secrets
  * CloudFormation: similar checks
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InfraFinding:
    severity: str
    file: str
    line: int
    rule: str
    description: str
    remediation: str
    snippet: str = ""


@dataclass
class InfraScanReport:
    scanner: str
    findings: list[InfraFinding] = field(default_factory=list)
    files_scanned: int = 0

    def summary(self) -> str:
        lines = [
            f"{self.scanner} scan ({self.files_scanned} files, {len(self.findings)} findings):",
        ]
        if not self.findings:
            lines.append("  ✓ No issues found.")
            return "\n".join(lines)
        for f in self.findings:
            lines.append(
                f"  [{f.severity:<8}] {f.file}:{f.line}  {f.rule}\n"
                f"    {f.description}\n"
                f"    Fix: {f.remediation}"
            )
        return "\n".join(lines)


# --- Dockerfile scanner --------------------------------------------------
def scan_dockerfile(path: Path) -> list[InfraFinding]:
    findings: list[InfraFinding] = []
    if not path.exists():
        return findings
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rel = str(path)
    has_healthcheck = False
    has_nonroot_user = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        stripped_upper = stripped.upper()
        if stripped_upper.startswith("FROM ") and ":LATEST" in stripped_upper:
            findings.append(InfraFinding(
                "LOW", rel, i, "latest-tag",
                "Using :latest tag — not reproducible",
                "Pin to a specific version tag",
                line.strip(),
            ))
        if stripped.startswith("USER "):
            has_nonroot_user = True
        if stripped.startswith("HEALTHCHECK"):
            has_healthcheck = True
        # Secrets in ENV.
        if re.match(r"ENV\s+\w*(KEY|SECRET|PASSWORD|TOKEN)\w*\s*=", stripped, re.IGNORECASE):
            findings.append(InfraFinding(
                "HIGH", rel, i, "secret-in-env",
                "Secret baked into image via ENV",
                "Mount secrets at runtime via env vars or secrets manager",
                line.strip(),
            ))
        # apt-get without no-install-recommends.
        if "APT-GET INSTALL" in stripped and "NO-INSTALL-RECOMMENDS" not in stripped:
            findings.append(InfraFinding(
                "LOW", rel, i, "apt-recommends",
                "apt-get install without --no-install-recommends — larger image",
                "Add --no-install-recommends",
                line.strip(),
            ))
        # curl | bash.
        if "CURL" in stripped and "|" in stripped and ("SH" in stripped or "BASH" in stripped):
            findings.append(InfraFinding(
                "HIGH", rel, i, "curl-pipe-bash",
                "curl | bash — unsafe install pattern",
                "Download, verify checksum, then run",
                line.strip(),
            ))
    if not has_nonroot_user:
        findings.append(InfraFinding(
            "MEDIUM", rel, 0, "no-nonroot-user",
            "Container runs as root by default",
            "Add a non-root USER instruction",
        ))
    if not has_healthcheck:
        findings.append(InfraFinding(
            "LOW", rel, 0, "no-healthcheck",
            "No HEALTHCHECK instruction",
            "Add a HEALTHCHECK for orchestration",
        ))
    return findings


# --- Terraform scanner ---------------------------------------------------
def scan_terraform(path: Path) -> list[InfraFinding]:
    findings: list[InfraFinding] = []
    if not path.exists() or path.suffix not in (".tf", ".hcl"):
        return findings
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    rel = str(path)
    for i, line in enumerate(lines, 1):
        # Public S3 bucket.
        if "acl" in line.lower() and "public-read" in line.lower():
            findings.append(InfraFinding(
                "CRITICAL", rel, i, "public-bucket",
                "S3 bucket with public-read ACL",
                "Use acl = \"private\" and CloudFront for public access",
                line.strip(),
            ))
        # 0.0.0.0/0 ingress.
        if "0.0.0.0/0" in line:
            findings.append(InfraFinding(
                "HIGH", rel, i, "open-ingress",
                "Security group ingress open to 0.0.0.0/0",
                "Restrict to known CIDR ranges",
                line.strip(),
            ))
        # Hardcoded secrets.
        if re.search(r"(secret|password|token)\s*=\s*\"[^\"]+\"", line, re.IGNORECASE):
            if not line.strip().startswith("#") and "var." not in line and "data." not in line:
                findings.append(InfraFinding(
                    "HIGH", rel, i, "hardcoded-secret",
                    "Hardcoded secret in Terraform",
                    "Use variables and a secrets backend",
                    line.strip(),
                ))
        # TLS disabled.
        if "ssl_protocol" in line.lower() and "=-" not in line and ("ssl_v2" in line.lower() or "ssl_v3" in line.lower() or "tls_1_0" in line.lower()):
            findings.append(InfraFinding(
                "MEDIUM", rel, i, "weak-tls",
                "Weak TLS version configured",
                "Use TLS 1.2 or higher",
                line.strip(),
            ))
    return findings


# --- CloudFormation scanner ----------------------------------------------
def scan_cloudformation(path: Path) -> list[InfraFinding]:
    findings: list[InfraFinding] = []
    if not path.exists() or path.suffix not in (".json", ".yaml", ".yml"):
        return findings
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings
    rel = str(path)
    for i, line in enumerate(src.splitlines(), 1):
        if "0.0.0.0/0" in line:
            findings.append(InfraFinding(
                "HIGH", rel, i, "open-ingress",
                "Security group ingress open to 0.0.0.0/0",
                "Restrict to known CIDR ranges",
                line.strip(),
            ))
        if re.search(r"\"Password\"\s*:\s*\"[^\"]+\"", line, re.IGNORECASE):
            findings.append(InfraFinding(
                "HIGH", rel, i, "hardcoded-secret",
                "Hardcoded password in CloudFormation",
                "Use AWS Secrets Manager or SSM Parameter Store",
                line.strip(),
            ))
    return findings


def scan_infrastructure(root: Path | str) -> list[InfraScanReport]:
    """Scan all Dockerfiles, Terraform and CloudFormation files in a project."""
    root = Path(root)
    reports: list[InfraScanReport] = []

    # Dockerfiles.
    dockerfiles = list(root.rglob("Dockerfile*")) + list(root.rglob("*.dockerfile"))
    if dockerfiles:
        report = InfraScanReport(scanner="Dockerfile")
        report.files_scanned = len(dockerfiles)
        for f in dockerfiles:
            report.findings.extend(scan_dockerfile(f))
        reports.append(report)

    # Terraform.
    tf_files = list(root.rglob("*.tf")) + list(root.rglob("*.hcl"))
    if tf_files:
        report = InfraScanReport(scanner="Terraform")
        report.files_scanned = len(tf_files)
        for f in tf_files:
            report.findings.extend(scan_terraform(f))
        reports.append(report)

    # CloudFormation.
    cf_files = [f for f in root.rglob("*") if f.suffix in (".json", ".yaml", ".yml")
                and any(k in f.name.lower() for k in ("cloudformation", "template", "stack"))]
    if cf_files:
        report = InfraScanReport(scanner="CloudFormation")
        report.files_scanned = len(cf_files)
        for f in cf_files:
            report.findings.extend(scan_cloudformation(f))
        reports.append(report)

    return reports
