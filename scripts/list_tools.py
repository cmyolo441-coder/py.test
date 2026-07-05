#!/usr/bin/env python3
"""Print a formatted catalog of all registered tools (no API calls).

Run with: python scripts/list_tools.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools import build_default_registry


def main() -> None:
    reg = build_default_registry()
    tools = sorted(reg.all(), key=lambda t: t.name)
    print(f"{len(tools)} tools registered:\n")
    for t in tools:
        flag = " [dangerous]" if t.dangerous else ""
        print(f"  {t.name:<18}{flag}")
        print(f"    {t.description}")


if __name__ == "__main__":
    main()
