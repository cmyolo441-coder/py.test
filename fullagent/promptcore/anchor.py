"""anchor - recency block: strongest standing rules + this turn's
relevant rules, framed as the prompt's own voice. Budgeted."""
from __future__ import annotations

from typing import List

from .lexicon import Directive
from .salience import ranked

NL = chr(10)
HEAD = "Your operating instructions still in force (from your system prompt above):"


def build_anchor(standing: List[Directive], turn_rules: List[Directive],
                  max_chars: int = 2200) -> str:
    seen = set()
    ordered: List[Directive] = []
    for d in list(turn_rules) + [d for d, _ in ranked(standing)]:
        if d.key in seen:
            continue
        seen.add(d.key)
        ordered.append(d)
    if not ordered:
        return ""
    parts = [HEAD]
    size = len(HEAD)
    for d in ordered:
        item = "- " + d.text
        if size + len(item) + 1 > max_chars:
            break
        parts.append(item)
        size += len(item) + 1
    return NL.join(parts) if len(parts) > 1 else ""


def _self_test() -> None:
    a = Directive(1, "Always reply in Hinglish.", "R", 0.1, 3, False, "reply")
    b = Directive(9, "Never use emojis.", "R", 0.9, 3, True, "reply")
    out = build_anchor([a, b], [b])
    assert out.startswith(HEAD)
    assert out.count("Never use emojis.") == 1
    assert out.index("Never use emojis.") < out.index("Always reply in Hinglish.")
    assert build_anchor([], []) == ""
    tiny = build_anchor([a, b], [], max_chars=len(HEAD) + 25)
    assert len(tiny) <= len(HEAD) + 25
    print("anchor ok")


if __name__ == "__main__":
    _self_test()
