"""Docker orchestration — compose file generation, container management.

Real, working Docker operations:
  * Generate docker-compose.yml from a project spec
  * Generate Dockerfile from language detection
  * List/inspect running containers
  * Build/up/down compose services
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: str = ".", timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


@dataclass
class ComposeService:
    name: str
    image: str = ""
    build: str = ""
    ports: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    command: str = ""


def generate_compose(services: list[ComposeService], version: str = "3.9") -> str:
    """Generate a docker-compose.yml from service specs."""
    lines = [f"version: '{version}'", "", "services:"]
    for svc in services:
        lines.append(f"  {svc.name}:")
        if svc.build:
            lines.append(f"    build: {svc.build}")
        elif svc.image:
            lines.append(f"    image: {svc.image}")
        if svc.command:
            lines.append(f"    command: {svc.command}")
        for port in svc.ports:
            lines.append(f"    - \"{port}\"")
        if svc.ports:
            lines.insert(-len(svc.ports), "    ports:")
        if svc.environment:
            lines.append("    environment:")
            for k, v in svc.environment.items():
                lines.append(f"      {k}: {v}")
        if svc.volumes:
            lines.append("    volumes:")
            for v in svc.volumes:
                lines.append(f"      - {v}")
        if svc.depends_on:
            lines.append("    depends_on:")
            for d in svc.depends_on:
                lines.append(f"      - {d}")
        lines.append("")
    return "\n".join(lines)


def write_compose(services: list[ComposeService], root: str = ".") -> str:
    """Generate and write docker-compose.yml. Returns the path."""
    content = generate_compose(services)
    path = Path(root) / "docker-compose.yml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def generate_dockerfile(language: str = "python", port: int = 8000, root: str = ".") -> str:
    """Generate a Dockerfile for the given language."""
    if language == "python":
        content = f"""FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \\
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port}/')"

RUN useradd -m -u 1000 appuser
USER appuser

CMD ["python", "main.py"]
"""
    elif language == "node":
        content = f"""FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY . .

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \\
  CMD curl -f http://localhost:{port}/ || exit 1

USER node

CMD ["node", "index.js"]
"""
    else:
        content = f"""FROM ubuntu:22.04

WORKDIR /app
COPY . .

EXPOSE {port}
CMD ["echo", "custom runtime — edit Dockerfile"]
"""
    path = Path(root) / "Dockerfile"
    path.write_text(content, encoding="utf-8")
    return str(path)


def compose_up(root: str = ".", detach: bool = True) -> tuple[bool, str]:
    args = ["docker", "compose", "up"]
    if detach:
        args.append("-d")
    return _run(args, cwd=root, timeout=120)


def compose_down(root: str = ".", volumes: bool = False) -> tuple[bool, str]:
    args = ["docker", "compose", "down"]
    if volumes:
        args.append("-v")
    return _run(args, cwd=root, timeout=60)


def compose_ps(root: str = ".") -> str:
    ok, out = _run(["docker", "compose", "ps"], cwd=root)
    return out if ok else "(docker compose ps failed)"


def compose_logs(service: str = "", root: str = ".", lines: int = 50) -> str:
    args = ["docker", "compose", "logs", f"--tail={lines}"]
    if service:
        args.append(service)
    ok, out = _run(args, cwd=root, timeout=30)
    return out if ok else "(docker compose logs failed)"


def list_containers() -> str:
    ok, out = _run(["docker", "ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"])
    return out if ok else "(docker ps failed — is Docker running?)"
