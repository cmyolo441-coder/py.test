"""Hot reload — auto-reload agent modules when source files change.

Watches the agent/ directory for .py file changes and reloads the affected
modules in-place. This lets you edit code and see changes without
restarting the agent.

Uses a background thread with a polling file watcher (stdlib only).
"""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

from .logging_config import get_logger

log = get_logger("agent.hot_reload")


class HotReloader:
    """Watches agent source files and reloads them on change."""

    def __init__(self, watch_dir: Path | str = "agent") -> None:
        self.watch_dir = Path(watch_dir)
        self._mtimes: dict[Path, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = False
        self.reload_count = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._enabled = True
        self._scan_mtimes()
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        log.info("Hot reload enabled, watching %s", self.watch_dir)

    def stop(self) -> None:
        self._enabled = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        log.info("Hot reload stopped")

    def toggle(self) -> bool:
        if self._enabled:
            self.stop()
        else:
            self.start()
        return self._enabled

    def _scan_mtimes(self) -> None:
        """Record the current mtimes of all .py files."""
        if not self.watch_dir.exists():
            return
        for path in self.watch_dir.rglob("*.py"):
            try:
                self._mtimes[path] = path.stat().st_mtime
            except OSError:
                pass

    def _watch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_for_changes()
            except Exception as exc:  # noqa: BLE001
                log.error("Hot reload error: %s", exc)
            self._stop.wait(timeout=2.0)

    def _check_for_changes(self) -> None:
        if not self.watch_dir.exists():
            return
        for path in self.watch_dir.rglob("*.py"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            old_mtime = self._mtimes.get(path)
            if old_mtime is None:
                self._mtimes[path] = mtime
                continue
            if mtime > old_mtime:
                self._mtimes[path] = mtime
                self._reload_module(path)

    def _reload_module(self, path: Path) -> None:
        """Reload the module corresponding to a changed file."""
        # Convert path to module name: agent/commands/foo.py -> agent.commands.foo
        try:
            rel = path.relative_to(self.watch_dir.parent)
        except ValueError:
            return
        module_name = str(rel).replace("/", ".").replace("\\", ".")
        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                self.reload_count += 1
                log.info("Reloaded module: %s", module_name)
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to reload %s: %s", module_name, exc)
        else:
            log.debug("Module %s not yet imported — will load on first use", module_name)

    def status(self) -> str:
        status = "ENABLED" if self._enabled else "DISABLED"
        return f"Hot reload: {status}  (watching {self.watch_dir}, {self.reload_count} reload(s) so far)"


_reloader: HotReloader | None = None
_reloader_lock = threading.Lock()


def get_hot_reloader() -> HotReloader:
    global _reloader
    if _reloader is None:
        with _reloader_lock:
            if _reloader is None:
                _reloader = HotReloader()
    return _reloader
