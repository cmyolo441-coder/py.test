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


def extract_rules(text: str, max_rules: int = 40) -> List[str]:
    """Return up to max_rules short instruction lines, in original order."""
    out: List[str] = []
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
        if low in seen:
            continue
        seen.add(low)
        out.append(line)
        if len(out) >= max_rules:
            break
    return out


def checklist(text: str, max_chars: int = 1400) -> str:
    """Compact reminder block (<1500 chars) for the END of the system prompt."""
    rules = extract_rules(text)
    if not rules:
        return ""
    head = "Reminder of your instructions:"
    parts = [head]
    size = len(head)
    for r in rules:
        item = "- " + r
        if size + len(item) + 1 > max_chars:
            break
        parts.append(item)
        size += len(item) + 1
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
