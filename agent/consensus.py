"""Multi-model consensus — query several models and pick the best answer.

For high-stakes decisions (Goal Mode verification, code reviews, architecture
choices) it's valuable to ask N independent models and pick the response that
survives cross-checking. This module runs the same prompt against multiple
providers concurrently and applies a scoring/voting strategy.

Strategies:
  * ``first``       — return the fastest response (default, cheap)
  * ``majority``    — return the response most models agree on (semantic vote)
  * ``longest``     — return the most detailed response
  * ``scored``      — run a judge model to pick the best (most expensive)
"""
from __future__ import annotations

import concurrent.futures
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .logging_config import get_logger

log = get_logger("agent.consensus")


@dataclass
class ConsensusResult:
    text: str
    provider: str
    model: str
    strategy: str
    all_responses: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0


def _query_one(
    provider_getter: Callable[[str, str], Any],
    chat_fn: Callable[[Any, list[dict]], str],
    provider: str,
    model: str,
    messages: list[dict],
) -> dict[str, Any]:
    import time
    start = time.perf_counter()
    try:
        text = chat_fn(provider_getter(provider, model), messages)
        return {
            "provider": provider,
            "model": model,
            "text": text,
            "ok": True,
            "duration_s": time.perf_counter() - start,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "provider": provider,
            "model": model,
            "text": "",
            "ok": False,
            "duration_s": time.perf_counter() - start,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_consensus(
    provider_getter: Callable[[str, str], Any],
    chat_fn: Callable[[Any, list[dict]], str],
    messages: list[dict],
    candidates: list[tuple[str, str]],
    strategy: str = "first",
    judge_fn: Callable[[list[dict]], str] | None = None,
    timeout: float = 60.0,
) -> ConsensusResult:
    """Query every (provider, model) in ``candidates`` and pick a winner.

    ``candidates`` is a list of (provider, model) pairs. At least 2 recommended.
    """
    import time
    start = time.perf_counter()
    if not candidates:
        return ConsensusResult(
            text="(no candidates)", provider="none", model="none",
            strategy=strategy, duration_s=0.0,
        )

    # Run all queries concurrently.
    responses: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        futures = {
            pool.submit(_query_one, provider_getter, chat_fn, prov, model, messages): (prov, model)
            for prov, model in candidates
        }
        for fut in concurrent.futures.as_completed(futures, timeout=timeout):
            try:
                responses.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                prov, model = futures[fut]
                responses.append({
                    "provider": prov, "model": model, "text": "",
                    "ok": False, "error": str(exc), "duration_s": 0.0,
                })

    ok_responses = [r for r in responses if r["ok"] and r["text"]]
    if not ok_responses:
        return ConsensusResult(
            text=f"(all {len(candidates)} candidates failed)",
            provider="none", model="none", strategy=strategy,
            all_responses=responses, duration_s=time.perf_counter() - start,
        )

    winner = _pick_winner(ok_responses, strategy, judge_fn)
    return ConsensusResult(
        text=winner["text"],
        provider=winner["provider"],
        model=winner["model"],
        strategy=strategy,
        all_responses=responses,
        duration_s=time.perf_counter() - start,
    )


def _pick_winner(
    responses: list[dict[str, Any]],
    strategy: str,
    judge_fn: Callable[[list[dict]], str] | None,
) -> dict[str, Any]:
    if strategy == "first":
        # Fastest successful response.
        return min(responses, key=lambda r: r["duration_s"])
    if strategy == "longest":
        # Most detailed (longest text).
        return max(responses, key=lambda r: len(r["text"]))
    if strategy == "majority":
        # Pick the response whose text most others share significant overlap with.
        best, best_score = responses[0], -1
        for r in responses:
            score = sum(_overlap(r["text"], other["text"]) for other in responses if other is not r)
            if score > best_score:
                best, best_score = r, score
        return best
    if strategy == "scored":
        if judge_fn is None:
            # Without a judge, fall back to longest.
            return max(responses, key=lambda r: len(r["text"]))
        # Judge picks index of best.
        try:
            picked = judge_fn(responses)
            for r in responses:
                if r["text"] == picked:
                    return r
        except Exception:  # noqa: BLE001
            pass
        return max(responses, key=lambda r: len(r["text"]))
    # Unknown strategy — default to first.
    return min(responses, key=lambda r: r["duration_s"])


def _overlap(a: str, b: str) -> float:
    """Cheap semantic overlap score: fraction of shared significant words."""
    if not a or not b:
        return 0.0
    wa = set(w.lower() for w in a.split() if len(w) > 4)
    wb = set(w.lower() for w in b.split() if len(w) > 4)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def describe_strategies() -> str:
    return (
        "Consensus strategies:\n"
        "  first    — return the fastest successful response (default)\n"
        "  majority — return the response most models agree on\n"
        "  longest  — return the most detailed response\n"
        "  scored   — run a judge model to pick the best"
    )
