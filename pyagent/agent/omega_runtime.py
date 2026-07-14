"""Omega runtime intelligence.

Detects project runtime surfaces: entrypoints, package metadata, dependencies,
Makefile targets, CI files and suggested local commands.
"""
from __future__ import annotations

import configparser
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RuntimeSurface:
    root: str
    entrypoints: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    optional_dependencies: dict[str, list[str]] = field(default_factory=dict)
    make_targets: list[str] = field(default_factory=list)
    ci_files: list[str] = field(default_factory=list)
    docker_files: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        return {
            "entrypoints": len(self.entrypoints),
            "config_files": len(self.config_files),
            "dependencies": len(self.dependencies),
            "optional_groups": len(self.optional_dependencies),
            "make_targets": len(self.make_targets),
            "ci_files": len(self.ci_files),
            "docker_files": len(self.docker_files),
            "scripts": len(self.scripts),
            "commands": self.commands,
        }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def analyze_runtime(root: str | Path = ".") -> RuntimeSurface:
    """Analyze runtime/package/build surfaces for a local project."""
    root_path = Path(root).resolve()
    surface = RuntimeSurface(root=str(root_path))
    known_configs = ["pyproject.toml", "requirements.txt", "pytest.ini", "setup.cfg", "tox.ini", "Dockerfile", "Makefile", "package.json", ".gitlab-ci.yml"]
    for name in known_configs:
        p = root_path / name
        if p.exists():
            surface.config_files.append(name)
    for p in (root_path / ".github" / "workflows").glob("*.yml") if (root_path / ".github" / "workflows").exists() else []:
        surface.ci_files.append(_rel(p, root_path))
    if (root_path / ".gitlab-ci.yml").exists():
        surface.ci_files.append(".gitlab-ci.yml")
    for p in root_path.glob("Dockerfile*"):
        surface.docker_files.append(_rel(p, root_path))
    surface.entrypoints.extend(_detect_entrypoints(root_path))
    surface.dependencies.extend(_read_dependencies(root_path))
    surface.optional_dependencies.update(_read_optional_dependencies(root_path))
    surface.make_targets.extend(_read_make_targets(root_path))
    surface.scripts.extend(_read_scripts(root_path))
    surface.commands = _suggest_commands(surface)
    return surface


def _detect_entrypoints(root: Path) -> list[str]:
    candidates: list[str] = []
    for name in ["main.py", "app.py", "cli.py"]:
        if (root / name).exists():
            candidates.append(name)
    scripts = root / "scripts"
    if scripts.exists():
        for p in sorted(scripts.glob("*.py"))[:20]:
            candidates.append(_rel(p, root))
    try:
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")) if (root / "pyproject.toml").exists() else {}
        for script, target in pyproject.get("project", {}).get("scripts", {}).items():
            candidates.append(f"script:{script} -> {target}")
    except (OSError, tomllib.TOMLDecodeError):
        pass
    return list(dict.fromkeys(candidates))


def _read_dependencies(root: Path) -> list[str]:
    deps: list[str] = []
    req = root / "requirements.txt"
    if req.exists():
        try:
            for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    deps.append(stripped)
        except OSError:
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            deps.extend(data.get("project", {}).get("dependencies", []) or [])
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return list(dict.fromkeys(deps))


def _read_optional_dependencies(root: Path) -> dict[str, list[str]]:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return {}
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        optional = data.get("project", {}).get("optional-dependencies", {}) or {}
        return {str(k): list(v) for k, v in optional.items()}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _read_make_targets(root: Path) -> list[str]:
    make = root / "Makefile"
    if not make.exists():
        return []
    try:
        content = make.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    targets = []
    for match in re.finditer(r"^([A-Za-z0-9_.-]+):", content, flags=re.MULTILINE):
        target = match.group(1)
        if not target.startswith("."):
            targets.append(target)
    return list(dict.fromkeys(targets))[:40]


def _read_scripts(root: Path) -> list[str]:
    scripts: list[str] = []
    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            for name, cmd in (data.get("scripts") or {}).items():
                scripts.append(f"npm:{name} -> {cmd}")
        except (OSError, json.JSONDecodeError):
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            for name, target in (data.get("project", {}).get("scripts", {}) or {}).items():
                scripts.append(f"python:{name} -> {target}")
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return scripts


def _suggest_commands(surface: RuntimeSurface) -> list[str]:
    commands: list[str] = []
    if "requirements.txt" in surface.config_files:
        commands.append("python -m pip install -r requirements.txt")
    if "pyproject.toml" in surface.config_files:
        commands.append("python -m pip install -e .")
    if any(t == "test" for t in surface.make_targets):
        commands.append("make test")
    else:
        commands.append("python -m pytest -q")
    if any(t == "health" for t in surface.make_targets):
        commands.append("make health")
    elif "scripts/healthcheck.py" in surface.entrypoints:
        commands.append("python scripts/healthcheck.py")
    commands.append("python -m compileall -q agent scripts tests")
    if surface.docker_files:
        commands.append("docker build -t terminal-agent .")
    return list(dict.fromkeys(commands))


def runtime_brief(surface: RuntimeSurface) -> str:
    """Render a concise runtime brief."""
    lines = ["Runtime surface:"]
    lines.append(f"  entrypoints: {', '.join(surface.entrypoints[:8]) or 'none'}")
    lines.append(f"  configs: {', '.join(surface.config_files) or 'none'}")
    lines.append(f"  deps: {len(surface.dependencies)} direct dependency entries")
    lines.append(f"  commands: {'; '.join(surface.commands[:6])}")
    return "\n".join(lines)
