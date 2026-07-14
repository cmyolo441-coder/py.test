"""Nova execution planning (safe, no command execution)."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandSpec:
    command: str
    purpose: str
    risk: str = "low"
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    commands: list[CommandSpec] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        return {"commands": len(self.commands), "high_risk": sum(1 for c in self.commands if c.risk == "high")}


def assess_command(command: str) -> str:
    """Return low/medium/high risk classification for a shell command string."""
    lowered = command.lower()
    dangerous = ["rm -rf", "mkfs", "dd if=", ":(){", "shutdown", "reboot", "chmod -r 777", "chown -r"]
    if any(d in lowered for d in dangerous):
        return "high"
    if any(k in lowered for k in ["docker", "pip install", "npm install", "git push", "curl", "wget"]):
        return "medium"
    return "low"


def build_execution_plan(commands: list[str]) -> ExecutionPlan:
    specs: list[CommandSpec] = []
    for cmd in commands:
        risk = assess_command(cmd)
        purpose = infer_purpose(cmd)
        prereq = []
        if "pytest" in cmd:
            prereq.append("dependencies installed")
        if cmd.startswith("docker"):
            prereq.append("docker daemon available")
        specs.append(CommandSpec(cmd, purpose, risk, prereq))
    return ExecutionPlan(specs)


def infer_purpose(command: str) -> str:
    c = command.lower()
    if "pytest" in c or "make test" in c:
        return "run tests"
    if "compileall" in c:
        return "compile/import syntax check"
    if "healthcheck" in c or "make health" in c:
        return "run project healthcheck"
    if "pip install" in c:
        return "install dependencies"
    if c.startswith("docker build"):
        return "build container image"
    if "lint" in c or "ruff" in c:
        return "lint code"
    return "execute verification command"


def shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def execution_context(plan: ExecutionPlan) -> str:
    lines = ["execution plan (safe preview):"]
    for spec in plan.commands[:12]:
        prereq = f" prereq={spec.prerequisites}" if spec.prerequisites else ""
        lines.append(f"- [{spec.risk}] {spec.command} — {spec.purpose}{prereq}")
    return "\n".join(lines)


def reorder_for_speed(plan: ExecutionPlan) -> ExecutionPlan:
    order = {"low": 0, "medium": 1, "high": 2}
    return ExecutionPlan(sorted(plan.commands, key=lambda c: (order.get(c.risk, 9), len(c.command))))
