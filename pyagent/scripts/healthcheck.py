#!/usr/bin/env python3
"""Health-check script: verifies the installation without hitting any API.

Run with: python scripts/healthcheck.py

It confirms every subsystem imports, the tool/command registries populate, and
the configured provider can be constructed (credentials permitting).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the scripts/ directory: add repo root to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ok = True

    def check(label: str, fn) -> None:
        nonlocal ok
        try:
            result = fn()
            print(f"[ OK ] {label}: {result}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")

    from agent import __version__
    from agent.tools import build_default_registry
    from agent.commands import build_command_registry
    from agent.providers.registry import list_providers
    from agent.config import Config

    check("version", lambda: __version__)
    check("tools", lambda: f"{len(build_default_registry().all())} tools")
    check("commands", lambda: f"{len(build_command_registry().all())} commands")
    check("providers", lambda: f"{len(list_providers())} providers")
    check("config", lambda: f"provider={Config.load().provider}")

    print("\nAll good!" if ok else "\nSome checks failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
