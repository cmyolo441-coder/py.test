"""Cheap deterministic extraction of instruction lines into a checklist."""
from __future__ import annotations

from typing import List

_KEYS = (
    "always", "never", "must", "do not", "don't", "dont", "should", "only",
    "format", "language", "tone", "length", "concise", "brief", "respond",
    "answer", "reply", "use ", "avoid", "prefer", "ensure", "required",
)
_BULLETS = "-*+•> \t"
_MAX_LINE = 200


def _clean(line: str) -> str:
    line = line.strip().lstrip(_BULLETS).strip()
    i = 0
    while i < len(line) and line[i].isdigit():
        i += 1
    if 0 < i < 4 and i < len(line) and line[i] in ".)":
        line = line[i + 1:].strip()
    return line.lstrip("#").strip()


def extract_rules(text: str, max_rules: int = 120) -> List[str]:
    """Return up to max_rules short instruction lines, in original order.

    Long prompts (tens of KB) carry far more than 40 instructions. When
    keyword-matching lines exceed the cap, sample from BOTH ends (half
    head, half tail): the closing rules of a long system prompt are the
    ones models most often forget, so they must reach the checklist."""
    candidates: List[str] = []
    seen = set()
    for raw in (text or "").split("\n"):
        line = _clean(raw)
        if len(line) < 8:
            continue
        low = line.lower()
        if not any(k in low for k in _KEYS):
            continue
        if len(line) > _MAX_LINE:
            cut = line.find(". ")
            line = line[: cut + 1] if 0 < cut <= _MAX_LINE else line[:_MAX_LINE].rstrip()
        low = line.lower()
        if low in seen:
            continue
        seen.add(low)
        candidates.append(line)
    if len(candidates) <= max_rules:
        return candidates
    # Over cap: first half of the budget from the top of the prompt,
    # the rest from the bottom — original order preserved.
    head_n = max_rules // 2
    tail_n = max_rules - head_n
    return candidates[:head_n] + candidates[-tail_n:]


def checklist(text: str, max_chars: int = 8000) -> str:
    """Reminder block (<=8000 chars) for the END of the system prompt.

    Sized for long custom prompts: all ~120 extracted rules fit, so a
    49KB prompt keeps its closing instructions salient too."""
    rules = extract_rules(text)
    if not rules:
        return ""
    head = "Reminder of your instructions:"
    parts = [head]
    size = len(head)
    # Reserve room for the FINAL rule: the closing instruction of a long
    # prompt is the one most often forgotten, so it must never be the
    # line dropped when the budget runs out.
    last_item = "- " + rules[-1]
    reserve = len(last_item) + 1 if len(rules) > 1 else 0
    for r in (rules[:-1] if len(rules) > 1 else []):
        item = "- " + r
        if size + len(item) + 1 + reserve > max_chars:
            break
        parts.append(item)
        size += len(item) + 1
    if len(rules) > 1 and size + len(last_item) + 1 <= max_chars:
        parts.append(last_item)
    elif len(rules) == 1:
        item = "- " + rules[0]
        if size + len(item) + 1 <= max_chars:
            parts.append(item)
    return "\n".join(parts) if len(parts) > 1 else ""


def _self_test() -> None:
    t = "# Title\nSome intro prose here.\n1. Always answer in French.\n- Never use emojis.\n- Never use emojis.\nKeep the tone friendly."
    r = extract_rules(t)
    assert r == ["Always answer in French.", "Never use emojis.", "Keep the tone friendly."], r
    c = checklist(t)
    assert c.startswith("Reminder of your instructions:") and len(c) < 1500
    assert checklist("nothing here") == ""
    assert len(extract_rules("always x y z q\n" * 100, 5)) == 1
    print("rules ok")


if __name__ == "__main__":
    _self_test()
