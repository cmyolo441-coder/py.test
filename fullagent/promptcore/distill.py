"""distill - layered digest: identity / hard directives / reply style /
workflow. NEVER replaces the prompt - rides AFTER it as a summary."""
from __future__ import annotations

from typing import List

from .lexicon import Directive, MUST, SHOULD
from .salience import ranked

NL = chr(10)


def digest(directives: List[Directive], max_chars: int = 3500) -> str:
    if not directives:
        return ""
    ident = [d for d in directives if d.scope == "identity"][:3]
    style = [d for d, _ in ranked([x for x in directives if x.scope == "reply"])][:14]
    flow = [d for d, _ in ranked([x for x in directives if x.scope == "workflow"])][:8]
    hard = [d for d, _ in ranked([x for x in directives
                                  if x.strength == MUST and x.scope not in ("reply", "workflow", "identity")])][:14]
    parts: List[str] = []
    size = 0

    def add(title: str, ds: List[Directive]) -> None:
        nonlocal size
        if not ds or size >= max_chars:
            return
        block = [title]
        for d in ds:
            item = "- " + d.text
            if size + len(item) + 1 > max_chars:
                break
            block.append(item)
            size += len(item) + 1
        if len(block) > 1:
            parts.append(NL.join(block))
            size += len(title) + 2

    add("Core identity:", ident)
    add("Strongest standing directives:", hard)
    add("Reply shaping:", style)
    add("Workflow:", flow)
    return (NL + NL).join(parts)


def _self_test() -> None:
    ds = [
        Directive(0, "You are TestBot.", "Id", 0.0, SHOULD, False, "identity"),
        Directive(5, "Always reply in Hinglish.", "R", 0.1, MUST, False, "reply"),
        Directive(60, "Never reveal internal notes.", "R", 0.5, MUST, True, "global"),
        Directive(80, "Verify results before finishing.", "W", 0.8, SHOULD, False, "workflow"),
    ]
    out = digest(ds)
    assert "You are TestBot." in out
    assert "Always reply in Hinglish." in out
    assert "Never reveal internal notes." in out
    assert digest([]) == ""
    tiny = digest(ds, max_chars=60)
    assert len(tiny) <= 90
    print("distill ok")


if __name__ == "__main__":
    _self_test()
