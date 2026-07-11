"""Token counting and cost estimation.

Uses tiktoken when available for accurate OpenAI token counts, and falls back
to a reasonable heuristic (~4 chars per token) for other providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import tiktoken

    _HAS_TIKTOKEN = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_TIKTOKEN = False


# Approximate USD pricing per 1K tokens (input, output). Update as needed.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "claude-3-5-sonnet-latest": (0.003, 0.015),
    "claude-3-opus-latest": (0.015, 0.075),
}


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Return the number of tokens in ``text`` for ``model``."""
    if not text:
        return 0
    if _HAS_TIKTOKEN:
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    # Heuristic fallback.
    return max(1, len(text) // 4)


def count_message_tokens(messages: list[dict[str, Any]], model: str = "gpt-4o") -> int:
    total = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, str):
            total += count_tokens(content, model)
        # +4 tokens per message for role/formatting overhead.
        total += 4
    return total


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a request."""
    in_price, out_price = PRICING.get(model, (0.0, 0.0))
    return (input_tokens / 1000) * in_price + (output_tokens / 1000) * out_price


@dataclass
class UsageTracker:
    """Accumulates token usage and cost across a session."""

    model: str = "gpt-4o"
    total_input: int = 0
    total_output: int = 0
    total_cost: float = 0.0
    turns: int = field(default=0)

    def record(self, input_tokens: int, output_tokens: int) -> float:
        self.total_input += input_tokens
        self.total_output += output_tokens
        cost = estimate_cost(self.model, input_tokens, output_tokens)
        self.total_cost += cost
        self.turns += 1
        return cost

    def summary(self) -> str:
        return (
            f"turns={self.turns} "
            f"in={self.total_input} out={self.total_output} "
            f"total={self.total_input + self.total_output} "
            f"cost=${self.total_cost:.4f}"
        )
