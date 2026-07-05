"""Environment profiles (dev / staging / prod) with layered settings.

Lets operators tune behaviour per environment without code changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    log_level: str
    json_logs: bool
    cache_ttl: float
    rate_per_second: float
    rate_capacity: int
    max_retries: int


PROFILES: dict[str, Profile] = {
    "dev": Profile("dev", "DEBUG", False, 60.0, 100.0, 200, 2),
    "staging": Profile("staging", "INFO", True, 300.0, 20.0, 40, 3),
    "prod": Profile("prod", "WARNING", True, 600.0, 10.0, 20, 5),
}


def current_profile() -> Profile:
    name = os.getenv("APP_ENV", "dev").lower()
    return PROFILES.get(name, PROFILES["dev"])
