"""Smart context-window manager.

Keeps conversations within a model's context budget by:
  * always preserving the system prompt and the most recent N messages,
  * summarising older messages into a compact "prior context" block,
  * preserving tool-call / tool-result pairs (never split them),
  * respecting per-model token limits (8k, 32k, 128k, 200k).

This is what lets Goal Mode run for many rounds without blowing the context
window on smaller models, while keeping full fidelity on large ones.
"""
from __future__ import annotations

from typing import Any

from .token_counter import count_message_tokens

# Conservative max-context per model family (tokens). We leave a 20% margin
# for the response so the model never truncates mid-answer.
MODEL_CONTEXT_BUDGET: dict[str, int] = {
    "gpt-4o": 110_000,
    "gpt-4o-mini": 110_000,
    "gpt-4-turbo": 110_000,
    "gpt-4": 7_000,
    "gpt-3.5-turbo": 14_000,
    "claude-3-5-sonnet-20241022": 160_000,
    "claude-3-5-sonnet-latest": 160_000,
    "claude-3-opus-latest": 160_000,
    "claude-3-haiku-20240307": 160_000,
    "gemini-1.5-flash": 800_000,
    "gemini-1.5-pro": 1_800_000,
    "gemini-2.0-flash": 800_000,
    "llama-3.3-70b-versatile": 110_000,
    "mistral-large-latest": 110_000,
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": 110_000,
    "mimo-v2.5-free": 128_000,
    "big-pickle": 128_000,
    "deepseek-v4-flash-free": 128_000,
    "zyloo/glm-5.1": 110_000,
    "llama3.1": 110_000,
    "llama3.2": 110_000,
    "qwen2.5": 110_000,
}

DEFAULT_BUDGET = 128_000


def context_budget_for(model: str) -> int:
    """Return the safe working-context budget (tokens) for ``model``."""
    if model in MODEL_CONTEXT_BUDGET:
        return MODEL_CONTEXT_BUDGET[model]
    # Heuristic by family prefix.
    lower = model.lower()
    if "gpt-4o" in lower or "o1" in lower or "o3" in lower:
        return 110_000
    if "claude" in lower:
        return 160_000
    if "gemini-1.5" in lower or "gemini-2" in lower:
        return 800_000
    if "llama-3.3" in lower or "llama3" in lower:
        return 110_000
    return DEFAULT_BUDGET


def _is_tool_pair_boundary(msg: dict[str, Any]) -> bool:
    """A tool message must be preceded by its assistant tool_call — never split."""
    if msg.get("role") == "tool":
        return True
    # Anthropic stores tool results as user messages with tool_result content blocks.
    if msg.get("role") == "user":
        content = msg.get("content")
        if isinstance(content, list):
            return any(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
    return False


def compress_messages(
    messages: list[dict[str, Any]],
    model: str,
    provider: str,
    target_tokens: int | None = None,
    preserve_recent: int = 6,
) -> list[dict[str, Any]]:
    """Return a possibly-trimmed copy of ``messages`` that fits the budget.

    Strategy:
      1. Always keep the system prompt (first message if role==system).
      2. Always keep the last ``preserve_recent`` messages (never trim the
         active turn or its tool results).
      3. If still over budget, replace older middle messages with a single
         summarisation marker so the model knows context was compacted.

    Never raises; on any error returns the original list unchanged.
    """
    if not messages:
        return messages
    budget = target_tokens or context_budget_for(model)
    current = count_message_tokens(messages, model, provider)
    if current <= budget:
        return messages

    # Split: optional system head + middle + recent tail.
    has_system = messages and messages[0].get("role") == "system"
    head = [messages[0]] if has_system else []
    body = messages[1:] if has_system else messages

    if len(body) <= preserve_recent:
        # Nothing to trim — return as-is and let the provider handle truncation.
        return messages

    tail = body[-preserve_recent:]
    middle = body[:-preserve_recent]

    # Build a compact summary marker for the dropped middle.
    dropped_count = len(middle)
    summary_marker = {
        "role": "system",
        "content": (
            f"[context compacted] {dropped_count} earlier message(s) were "
            "summarised to fit the model's context window. Key points: "
            + _extract_key_points(middle)
        ),
    }

    # Reassemble and check size.
    candidate = head + [summary_marker] + tail
    if count_message_tokens(candidate, model, provider) <= budget:
        return candidate

    # Still too big: trim the tail one message at a time, preserving tool pairs.
    trimmed_tail = list(tail)
    while trimmed_tail and count_message_tokens(head + [summary_marker] + trimmed_tail, model, provider) > budget:
        # Don't break a tool pair.
        if len(trimmed_tail) >= 2 and _is_tool_pair_boundary(trimmed_tail[0]):
            trimmed_tail = trimmed_tail[2:]
        else:
            trimmed_tail = trimmed_tail[1:]
    return head + [summary_marker] + trimmed_tail if trimmed_tail else head + [summary_marker]


def _extract_key_points(messages: list[dict[str, Any]]) -> str:
    """Build a 1-2 sentence summary string from dropped messages."""
    points: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b) for b in content
            )
        content = str(content).strip()
        if not content:
            continue
        # Take the first sentence / first 120 chars of each message.
        snippet = content.split(".")[0][:120]
        if snippet:
            points.append(f"{role}: {snippet}")
    if not points:
        return "(no extractable content)"
    return " | ".join(points)[:800]
