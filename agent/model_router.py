"""Smart model router — picks the best model for a given query type.

Analyses the user's prompt and routes to the model that's strongest for that
task class, while respecting cost/latency preferences:

  * Coding / debug  -> Claude Sonnet or GPT-4o (strong reasoning)
  * Quick facts     -> GPT-4o-mini / Gemini Flash / Zen (fast, cheap)
  * Long context    -> Gemini 1.5 Pro (1M+ context)
  * Math / calc     -> GPT-4o or local Llama
  * Creative        -> Claude Sonnet
  * Local / private -> Ollama

Rules can be customised via Config.routing_rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Query-class -> recommended (provider, model) pairs, in priority order.
ROUTING_TABLE: dict[str, list[tuple[str, str]]] = {
    "coding": [
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("openai", "gpt-4o"),
        ("zen", "big-pickle"),
        ("ollama", "llama3.1"),
    ],
    "quick": [
        ("zen", "mimo-v2.5-free"),
        ("openai", "gpt-4o-mini"),
        ("gemini", "gemini-2.0-flash"),
        ("ollama", "llama3.1"),
    ],
    "long_context": [
        ("gemini", "gemini-1.5-pro"),
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("openai", "gpt-4o"),
    ],
    "math": [
        ("openai", "gpt-4o"),
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("ollama", "llama3.1"),
    ],
    "creative": [
        ("anthropic", "claude-3-5-sonnet-20241022"),
        ("openai", "gpt-4o"),
        ("zen", "big-pickle"),
    ],
    "local": [
        ("ollama", "llama3.1"),
    ],
    "default": [
        ("zen", "mimo-v2.5-free"),
        ("openai", "gpt-4o-mini"),
        ("ollama", "llama3.1"),
    ],
}

# Pattern signals used to classify the query.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("coding", re.compile(
        r"\b(code|function|class|bug|error|stack\s?trace|exception|debug|"
        r"refactor|implement|api|endpoint|sql|regex|algorithm|compile|"
        r"python|javascript|typescript|java|c\+\+|rust|go\b|ruby|swift|kotlin)\b",
        re.IGNORECASE,
    )),
    ("math", re.compile(
        r"\b(calculate|solve|equation|integral|derivative|matrix|prove|"
        r"theorem|probability|statistics|algebra|geometry)\b|"
        r"\d+\s*[+\-*/^]\s*\d+",
        re.IGNORECASE,
    )),
    ("long_context", re.compile(
        r"\b(summari[sz]e|analyze|review)\s+(this|the|a)?\s*(document|file|"
        r"book|paper|article|codebase|repository)|long\s+document|"
        r"entire\s+(file|codebase|project)\b",
        re.IGNORECASE,
    )),
    ("creative", re.compile(
        r"\b(write|compose|draft|create|story|poem|essay|blog|"
        r"screenplay|dialogue|character)\b",
        re.IGNORECASE,
    )),
    ("local", re.compile(
        r"\b(offline|local|private|no\s+internet|air-?gapped)\b",
        re.IGNORECASE,
    )),
    ("quick", re.compile(
        r"^(what|who|when|where|why|how|is|are|can|does|do|did)\b.*\?$",
        re.IGNORECASE,
    )),
]


@dataclass
class RoutingDecision:
    query_class: str
    provider: str
    model: str
    reason: str
    alternatives: list[tuple[str, str]]


def classify_query(prompt: str) -> tuple[str, str]:
    """Return (query_class, reason) for ``prompt``."""
    if not prompt:
        return "default", "empty prompt"
    for label, pattern in _PATTERNS:
        if pattern.search(prompt):
            return label, f"matched {label} pattern"
    return "default", "no specific signal — using default"


def route(
    prompt: str,
    available_providers: list[str] | None = None,
    preferred_provider: str | None = None,
) -> RoutingDecision:
    """Pick the best (provider, model) for ``prompt``.

    ``available_providers`` filters the table to only providers the user has
    configured. ``preferred_provider`` always wins if it has a model in the
    matched class.
    """
    query_class, reason = classify_query(prompt)
    candidates = ROUTING_TABLE.get(query_class, ROUTING_TABLE["default"])
    available = set(available_providers or [p for p, _ in candidates])

    # Honour an explicit preference if it's in the class table.
    if preferred_provider and preferred_provider in available:
        for prov, model in candidates:
            if prov == preferred_provider:
                return RoutingDecision(
                    query_class=query_class,
                    provider=prov,
                    model=model,
                    reason=f"preferred provider {prov} has a {query_class} model ({reason})",
                    alternatives=[(p, m) for p, m in candidates if p != prov],
                )

    # Otherwise pick the first available candidate.
    for prov, model in candidates:
        if prov in available:
            return RoutingDecision(
                query_class=query_class,
                provider=prov,
                model=model,
                reason=f"routed to {prov}/{model} for {query_class} ({reason})",
                alternatives=[(p, m) for p, m in candidates if p != prov],
            )

    # Fall back to default if nothing matched.
    return RoutingDecision(
        query_class="default",
        provider="zen",
        model="mimo-v2.5-free",
        reason="no configured provider matched — using free Zen default",
        alternatives=[],
    )


def describe_routing_table() -> str:
    lines = ["Smart routing table:"]
    for cls, pairs in ROUTING_TABLE.items():
        models = ", ".join(f"{p}:{m}" for p, m in pairs)
        lines.append(f"  {cls:<14} -> {models}")
    return "\n".join(lines)
