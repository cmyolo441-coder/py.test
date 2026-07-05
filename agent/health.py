"""Health checks and readiness probes.

Exposes a simple aggregate health status useful for container orchestration
and service monitoring.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Callable


@dataclass
class CheckResult:
    name: str
    healthy: bool
    detail: str = ""


class HealthMonitor:
    """Runs registered checks and aggregates their results."""

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[], CheckResult]] = {}

    def register(self, name: str, check: Callable[[], CheckResult]) -> None:
        self._checks[name] = check

    def run(self) -> dict[str, object]:
        results = [check() for check in self._checks.values()]
        healthy = all(r.healthy for r in results)
        return {
            "status": "healthy" if healthy else "unhealthy",
            "checks": [
                {"name": r.name, "healthy": r.healthy, "detail": r.detail}
                for r in results
            ],
        }


def disk_space_check(min_free_mb: int = 100) -> CheckResult:
    usage = shutil.disk_usage(".")
    free_mb = usage.free / (1024 * 1024)
    return CheckResult(
        name="disk_space",
        healthy=free_mb >= min_free_mb,
        detail=f"{free_mb:.0f} MB free",
    )
