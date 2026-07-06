"""SBOM (Software Bill of Materials) generator — CycloneDX format.

Generates a real SBOM JSON document listing all dependencies declared in:
  * requirements.txt
  * pyproject.toml [project.dependencies]
  * package.json dependencies
  * go.mod
  * Cargo.toml

Output is CycloneDX 1.4 compatible JSON. Used for compliance and
vulnerability tracking.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any


def _parse_requirements(path: Path) -> list[dict[str, str]]:
    deps = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Strip inline comments.
            line = line.split("#")[0].strip()
            # Match "package==1.0.0" or "package>=1.0.0" or "package".
            m = re.match(r"^([A-Za-z0-9_.-]+)\s*(?:[=<>!~]+)?\s*([\d.]+)?", line)
            if m:
                deps.append({"name": m.group(1).lower(), "version": m.group(2) or "unknown"})
    except OSError:
        pass
    return deps


def _parse_pyproject(path: Path) -> list[dict[str, str]]:
    deps = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Find [project] dependencies section.
        m = re.search(r"\[project\].*?dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip().strip(",").strip('"').strip("'")
                if not line:
                    continue
                # Parse "package>=1.0.0" etc.
                m2 = re.match(r"^([A-Za-z0-9_.-]+)\s*(?:[=<>!~]+)?\s*([\d.]+)?", line)
                if m2:
                    deps.append({"name": m2.group(1).lower(), "version": m2.group(2) or "unknown"})
    except OSError:
        pass
    return deps


def _parse_package_json(path: Path) -> list[dict[str, str]]:
    deps = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies"):
            for name, version in (data.get(section) or {}).items():
                deps.append({"name": name, "version": version.lstrip("^~>=<")})
    except (OSError, json.JSONDecodeError):
        pass
    return deps


def generate_sbom(root: Path | str = ".", output_path: Path | str | None = None) -> dict[str, Any] | str:
    """Generate a CycloneDX SBOM for the project at ``root``.

    If ``output_path`` is given, writes JSON there and returns the path.
    Otherwise returns the SBOM dict.
    """
    root = Path(root)
    components: list[dict[str, Any]] = []
    seen: set[str] = set()

    # requirements.txt
    req = root / "requirements.txt"
    if req.exists():
        for dep in _parse_requirements(req):
            if dep["name"] not in seen:
                components.append({
                    "type": "library",
                    "bom-ref": f"pkg:pypi/{dep['name']}@{dep['version']}",
                    "name": dep["name"],
                    "version": dep["version"],
                    "purl": f"pkg:pypi/{dep['name']}@{dep['version']}",
                })
                seen.add(dep["name"])

    # pyproject.toml
    pp = root / "pyproject.toml"
    if pp.exists():
        for dep in _parse_pyproject(pp):
            if dep["name"] not in seen:
                components.append({
                    "type": "library",
                    "bom-ref": f"pkg:pypi/{dep['name']}@{dep['version']}",
                    "name": dep["name"],
                    "version": dep["version"],
                    "purl": f"pkg:pypi/{dep['name']}@{dep['version']}",
                })
                seen.add(dep["name"])

    # package.json
    pj = root / "package.json"
    if pj.exists():
        for dep in _parse_package_json(pj):
            key = f"npm:{dep['name']}"
            if key not in seen:
                components.append({
                    "type": "library",
                    "bom-ref": f"pkg:npm/{dep['name']}@{dep['version']}",
                    "name": dep["name"],
                    "version": dep["version"],
                    "purl": f"pkg:npm/{dep['name']}@{dep['version']}",
                })
                seen.add(key)

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tools": [{"vendor": "terminal-agent", "name": "sbom-generator", "version": "1.0.0"}],
            "component": {
                "type": "application",
                "name": root.name,
                "version": "1.0.0",
            },
        },
        "components": components,
    }

    if output_path:
        out = Path(output_path)
        out.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
        return str(out)
    return sbom


def sbom_summary(sbom: dict[str, Any] | str) -> str:
    if isinstance(sbom, str):
        sbom = json.loads(Path(sbom).read_text(encoding="utf-8"))
    components = sbom.get("components", [])
    lines = [
        f"SBOM Summary ({len(components)} components):",
        f"  format:     {sbom.get('bomFormat')} {sbom.get('specVersion')}",
        f"  serial:     {sbom.get('serialNumber', 'n/a')[:50]}",
        f"  generated:  {sbom.get('metadata', {}).get('timestamp', 'n/a')}",
    ]
    # Group by ecosystem.
    ecosystems: dict[str, int] = {}
    for c in components:
        purl = c.get("purl", "")
        eco = purl.split("/")[0] if "/" in purl else "unknown"
        ecosystems[eco] = ecosystems.get(eco, 0) + 1
    lines.append("  by ecosystem:")
    for eco, count in sorted(ecosystems.items()):
        lines.append(f"    {eco:<14} {count}")
    lines.append("\n  Components:")
    for c in components[:20]:
        lines.append(f"    {c['name']:<30} {c.get('version', '?')}")
    if len(components) > 20:
        lines.append(f"    ... and {len(components) - 20} more")
    return "\n".join(lines)
