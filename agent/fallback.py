"""Model fallback chain — automatically retry on a list of providers/models.

When the primary model fails (rate limit, network, 500, etc.) the chain
advances to the next entry. This makes Goal Mode resilient: if the user's
preferred provider is down, the goal still completes on a free fallback
like Zen or a local Ollama.

Configuration via Config.fallback_chain (list of "provider:model" strings)
or the ``FALLBACK_CHAIN`` env var (comma-separated).
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .logging_config import get_logger

log = get_logger("agent.fallback")

# Sensible default chain: start free, escalate to paid if configured.
DEFAULT_CHAIN: list[str] = [
    "zen:mimo-v2.5-free",
    "zen:big-pickle",
    "zen:deepseek-v4-flash-free",
    "ollama:llama3.1",
    "openai:gpt-4o-mini",
    "anthropic:claude-3-5-sonnet-20241022",
]

# How long to remember a provider/model is "down" before retrying it.
COOLDOWN_SECONDS = 120.0


@dataclass
class ChainEntry:
    provider: str
    model: str
    last_failure: float = 0.0
    failure_count: int = 0

    @property
    def is_on_cooldown(self) -> bool:
        return (time.time() - self.last_failure) < COOLDOWN_SECONDS

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure = time.time()

    def record_success(self) -> None:
        self.failure_count = 0
        self.last_failure = 0.0


class FallbackChain:
    """Tracks a prioritised list of (provider, model) pairs with health state.

    The agent calls ``pick()`` to get the best currently-healthy entry, tries
    it, then calls ``record_failure`` / ``record_success`` based on outcome.
    """

    def __init__(self, entries: list[str] | None = None) -> None:
        raw = entries or _env_chain() or DEFAULT_CHAIN
        self.entries: list[ChainEntry] = []
        for spec in raw:
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            prov, model = spec.split(":", 1)
            self.entries.append(ChainEntry(provider=prov.strip(), model=model.strip()))
        self._lock = threading.Lock()
        self._position = 0

    def pick(self) -> ChainEntry | None:
        """Return the first non-cooled-down entry, advancing the cursor."""
        with self._lock:
            if not self.entries:
                return None
            # Try from current position, wrapping around once.
            n = len(self.entries)
            for offset in range(n):
                idx = (self._position + offset) % n
                entry = self.entries[idx]
                if not entry.is_on_cooldown:
                    self._position = idx
                    return entry
            # All on cooldown — return the least-recently-failed as last resort.
            return min(self.entries, key=lambda e: e.last_failure)

    def record_failure(self, provider: str, model: str) -> None:
        with self._lock:
            for entry in self.entries:
                if entry.provider == provider and entry.model == model:
                    entry.record_failure()
                    log.warning(
                        "fallback: %s/%s failed (%d time(s)), cooling down %ds",
                        provider, model, entry.failure_count, COOLDOWN_SECONDS,
                    )
                    # Advance cursor past the failed entry.
                    idx = self.entries.index(entry)
                    self._position = (idx + 1) % len(self.entries)
                    return

    def record_success(self, provider: str, model: str) -> None:
        with self._lock:
            for entry in self.entries:
                if entry.provider == provider and entry.model == model:
                    entry.record_success()
                    return

    def health_table(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "provider": e.provider,
                    "model": e.model,
                    "failures": e.failure_count,
                    "on_cooldown": e.is_on_cooldown,
                    "cooldown_remaining": max(0.0, COOLDOWN_SECONDS - (time.time() - e.last_failure))
                        if e.is_on_cooldown else 0.0,
                }
                for e in self.entries
            ]

    def describe(self) -> str:
        rows = self.health_table()
        if not rows:
            return "(no fallback chain configured)"
        lines = ["Fallback chain:"]
        for i, r in enumerate(rows, 1):
            status = "COOLDOWN" if r["on_cooldown"] else "OK"
            lines.append(
                f"  {i}. {r['provider']}:{r['model']}  [{status}]  "
                f"failures={r['failures']}"
            )
        return "\n".join(lines)


def _env_chain() -> list[str] | None:
    raw = os.getenv("FALLBACK_CHAIN")
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


# Process-wide singleton.
_chain: FallbackChain | None = None
_chain_lock = threading.Lock()


def get_fallback_chain() -> FallbackChain:
    global _chain
    if _chain is None:
        with _chain_lock:
            if _chain is None:
                _chain = FallbackChain()
    return _chain


def with_fallback(
    provider_getter: Callable[[str, str], Any],
    chat_fn: Callable[[Any, list[dict]], str],
    messages: list[dict],
    primary_provider: str,
    primary_model: str,
) -> tuple[str, str, str]:
    """Run ``chat_fn`` against the primary model; on failure, walk the chain.

    Returns (text, provider_used, model_used). Never raises — if every entry
    fails, returns an error string with provider="none".
    """
    chain = get_fallback_chain()
    # Try primary first.
    attempts = [(primary_provider, primary_model)] + [
        (e.provider, e.model) for e in chain.entries
        if not (e.provider == primary_provider and e.model == primary_model)
    ]
    last_error = ""
    for prov, model in attempts:
        try:
            provider = provider_getter(prov, model)
            text = chat_fn(provider, messages)
            if prov != primary_provider:
                log.info("fallback: succeeded on %s/%s after primary failed", prov, model)
            chain.record_success(prov, model)
            return text, prov, model
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            chain.record_failure(prov, model)
            continue
    return f"(all models failed: {last_error})", "none", "none"
