"""Feature flags — toggle features at runtime without restart.

Lets the user enable/disable experimental features dynamically. Flags
persist to ~/.terminal_agent/feature_flags.json so they survive restarts.

Use cases:
  * Roll out a new feature to test before enabling for everyone
  * Quickly disable a misbehaving feature
  * A/B test different behaviours
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


# All known feature flags with their default values.
DEFAULT_FLAGS: dict[str, bool] = {
    "rag_v2": False,  # Use embedding-based RAG for context
    "multi_agent": False,  # Enable multi-agent orchestration
    "self_reflection": False,  # Run self-reflection loop on responses
    "tool_learning": True,  # Track tool success/failure
    "long_term_memory": False,  # Persist facts across sessions
    "knowledge_graph": False,  # Build and query a codebase graph
    "auto_compact": True,  # Auto-compact context when near limit
    "smart_routing": False,  # Route prompts to best model
    "fallback_chain": True,  # Use fallback chain on provider failure
    "consensus_mode": False,  # Query multiple models for consensus
    "voice_input": False,  # Accept voice commands
    "voice_output": False,  # Speak responses aloud
    "browser_automation": False,  # Enable Playwright tools
    "scheduler": False,  # Enable background scheduled tasks
    "mcp_server": False,  # Expose tools via MCP
    "tool_creator": False,  # Allow agent to write new tools
    "prometheus_exporter": False,  # Serve /metrics endpoint
    "telemetry": False,  # Collect anonymous usage analytics
    "profiler": False,  # Profile turn timings
    "audit_log": True,  # Record immutable audit trail
    "cost_budget_enforce": False,  # Block turns when budget exceeded
    "quality_scorer": True,  # Score response quality
    "branching": False,  # Enable conversation branching
    "checkpoint_every_turn": True,  # Auto-save recovery checkpoints
    "boot_animation": True,  # Play animated boot sequence
}


class FeatureFlags:
    """Runtime-toggleable feature flags with persistence."""

    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._flags: dict[str, bool] = dict(DEFAULT_FLAGS)
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".terminal_agent" / "feature_flags.json"
        self._load()

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            return self._flags.get(name, False)

    def enable(self, name: str) -> bool:
        with self._lock:
            if name not in DEFAULT_FLAGS:
                return False
            self._flags[name] = True
            self._save()
            return True

    def disable(self, name: str) -> bool:
        with self._lock:
            if name not in DEFAULT_FLAGS:
                return False
            self._flags[name] = False
            self._save()
            return True

    def toggle(self, name: str) -> bool | None:
        with self._lock:
            if name not in DEFAULT_FLAGS:
                return None
            self._flags[name] = not self._flags[name]
            self._save()
            return self._flags[name]

    def set(self, name: str, value: bool) -> bool:
        with self._lock:
            if name not in DEFAULT_FLAGS:
                return False
            self._flags[name] = value
            self._save()
            return True

    def reset(self) -> None:
        with self._lock:
            self._flags = dict(DEFAULT_FLAGS)
            self._save()

    def all(self) -> dict[str, bool]:
        with self._lock:
            return dict(self._flags)

    def dashboard(self) -> str:
        with self._lock:
            lines = ["Feature flags:"]
            for name, default in DEFAULT_FLAGS.items():
                current = self._flags.get(name, default)
                icon = "✓" if current else "✗"
                default_icon = " (default)" if current == default else " (changed)"
                lines.append(f"  {icon} {name:<28}{default_icon}")
        lines.append("")
        lines.append("Use /flag <name> on|off to toggle.")
        return "\n".join(lines)

    def _save(self) -> None:
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            self.persist_path.write_text(json.dumps(self._flags, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            for name, value in data.items():
                if name in DEFAULT_FLAGS:
                    self._flags[name] = value
        except (json.JSONDecodeError, OSError):
            pass


_flags: FeatureFlags | None = None
_flags_lock = threading.Lock()


def get_feature_flags() -> FeatureFlags:
    global _flags
    if _flags is None:
        with _flags_lock:
            if _flags is None:
                _flags = FeatureFlags()
    return _flags


def is_enabled(name: str) -> bool:
    """Convenience function."""
    return get_feature_flags().is_enabled(name)
