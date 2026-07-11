"""Lightweight token estimation.

If ``tiktoken`` is available we use it for accurate counts; otherwise we fall
back to a heuristic (~4 chars/token, adjusted for whitespace) that is good
enough for context-budget decisions.
"""

from __future__ import annotations

from functools import lru_cache

try:
    import tiktoken

    _HAS_TIKTOKEN = True
except Exception:  # pragma: no cover
    _HAS_TIKTOKEN = False


@lru_cache(maxsize=8)
def _encoder(model: str):  # pragma: no cover - depends on optional dep
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def estimate_tokens(text: str, model: str = "gpt-4o") -> int:
    if not text:
        return 0
    if _HAS_TIKTOKEN:
        try:
            return len(_encoder(model).encode(text))
        except Exception:
            pass
    # Heuristic fallback.
    words = text.split()
    return max(len(text) // 4, int(len(words) * 1.3))


def estimate_messages(messages: list[dict], model: str = "gpt-4o") -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(b) for b in content)
        total += estimate_tokens(str(content), model) + 4  # per-message overhead
    return total
