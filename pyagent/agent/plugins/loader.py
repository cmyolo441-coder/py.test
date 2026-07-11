"""Dynamically load plugin tools from ~/.terminal_agent/plugins/*.py.

Each plugin module may expose a ``register()`` function returning a list of
``Tool`` objects (or a module-level ``TOOLS`` list). Errors in one plugin never
crash the app; they are logged and skipped.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ..tools.base import Tool
from ..utils.logging import get_logger

log = get_logger("agent.plugins")

PLUGINS_DIR = Path.home() / ".terminal_agent" / "plugins"


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"agent_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_plugins() -> list[Tool]:
    tools: list[Tool] = []
    if not PLUGINS_DIR.exists():
        return tools
    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _load_module(path)
            if module is None:
                continue
            if hasattr(module, "register") and callable(module.register):
                result = module.register()
            else:
                result = getattr(module, "TOOLS", [])
            for tool in result or []:
                if isinstance(tool, Tool):
                    tools.append(tool)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to load plugin %s: %s", path.name, exc)
    return tools
