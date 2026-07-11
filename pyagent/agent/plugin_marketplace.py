"""Plugin marketplace — discover, install and share plugins.

Plugins are Python files that define a ``register()`` function returning a
list of Tool objects. The marketplace lets users:
  * List available plugins (from a local catalog)
  * Install a plugin by copying it to ~/.terminal_agent/plugins/
  * Uninstall plugins
  * List installed plugins

For a real marketplace, this would fetch from a remote registry. Here we
provide a local catalog of built-in plugins that ship with the agent.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .logging_config import get_logger
from .plugins.loader import PLUGINS_DIR

log = get_logger("agent.plugin_marketplace")

# Built-in plugin catalog. Each entry: (name, description, source_path).
BUILTIN_CATALOG: list[dict[str, str]] = [
    {
        "name": "reverse_text",
        "description": "Reverse a string (example plugin)",
        "source": "agent/plugins/example_plugin.py",
    },
    {
        "name": "calculator",
        "description": "Safe arithmetic expression evaluator",
        "source": "agent/plugins/example_calculator.py",
    },
]


@dataclass
class InstalledPlugin:
    name: str
    path: Path
    size_bytes: int


def list_available() -> list[dict[str, str]]:
    """List plugins available in the catalog but not yet installed."""
    installed_names = {p.name for p in list_installed()}
    return [p for p in BUILTIN_CATALOG if p["name"] not in installed_names]


def list_installed() -> list[InstalledPlugin]:
    """List plugins installed in ~/.terminal_agent/plugins/."""
    if not PLUGINS_DIR.exists():
        return []
    plugins = []
    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        plugins.append(InstalledPlugin(
            name=path.stem,
            path=path,
            size_bytes=path.stat().st_size,
        ))
    return plugins


def install(name: str) -> tuple[bool, str]:
    """Install a plugin from the catalog by name."""
    catalog_entry = next((p for p in BUILTIN_CATALOG if p["name"] == name), None)
    if catalog_entry is None:
        return False, f"No plugin named '{name}' in the catalog"
    source = Path(catalog_entry["source"])
    if not source.is_absolute():
        # Try relative to the agent package.
        import agent
        source = Path(agent.__file__).parent.parent / catalog_entry["source"]
    if not source.exists():
        return False, f"Plugin source not found: {source}"
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    dest = PLUGINS_DIR / f"{name}.py"
    shutil.copy2(source, dest)
    log.info("Installed plugin '%s' to %s", name, dest)
    return True, f"Installed '{name}' to {dest}"


def uninstall(name: str) -> tuple[bool, str]:
    """Remove an installed plugin."""
    dest = PLUGINS_DIR / f"{name}.py"
    if not dest.exists():
        return False, f"Plugin '{name}' is not installed"
    dest.unlink()
    return True, f"Uninstalled '{name}'"


def install_all() -> dict[str, tuple[bool, str]]:
    """Install all available catalog plugins."""
    results: dict[str, tuple[bool, str]] = {}
    for plugin in list_available():
        results[plugin["name"]] = install(plugin["name"])
    return results


def marketplace_dashboard() -> str:
    installed = list_installed()
    available = list_available()
    lines = [
        "Plugin marketplace:",
        f"  installed: {len(installed)}",
        f"  available: {len(available)}",
        "",
        "Installed plugins:",
    ]
    if not installed:
        lines.append("  (none)")
    for p in installed:
        lines.append(f"  ✓ {p.name:<20} ({p.size_bytes} bytes)  {p.path}")
    lines.append("")
    lines.append("Available to install:")
    if not available:
        lines.append("  (all catalog plugins are installed)")
    for p in available:
        lines.append(f"  + {p['name']:<20} {p['description']}")
    lines.append("")
    lines.append("Use /plugin-install <name> to install, /plugin-uninstall <name> to remove.")
    return "\n".join(lines)
