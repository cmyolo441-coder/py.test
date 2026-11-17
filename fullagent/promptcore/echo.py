"""echo - per-turn relevant-rule selection. Token-overlap matching
(embedding-free, microseconds). Selected rules echo near the tail."""
from __future__ import annotations

import re
from typing import List, Tuple

from .lexicon import Directive
from .salience import score

_WORD = re.compile("[a-z]{3,}")
_STOP = frozenset(("the and for with that this what when where which how why "
                   "you your are was were will would should could can may "
                   "kya hai hain karo karna mera meri mere tum aap").split())


def tokens(text: str) -> set:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def select_for_query(query: str, directives: List[Directive],
                     k: int = 8) -> List[Directive]:
    """Top-k for this turn: query overlap first, salience tiebreak.
    Reply/identity rules always eligible; zero-overlap falls back to
    pure salience."""
    if not directives:
        return []
    qt = tokens(query or "")
    scored: List[Tuple[float, int, Directive]] = []
    for i, d in enumerate(directives):
        overlap = len(qt & tokens(d.text)) if qt else 0
        base = score(d)
        if d.scope in ("reply", "identity"):
            base += 1.0
        s = base + 2.5 * overlap
        scored.append((s, -i, d))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [d for _, _, d in scored[:k]]


def _self_test() -> None:
    ds = [
        Directive(0, "You are TestBot.", "Id", 0.0, 2, False, "identity"),
        Directive(5, "Always reply in Hinglish.", "R", 0.1, 3, False, "reply"),
        Directive(40, "Never delete database files without confirmation.", "T", 0.5, 3, True, "tool"),
        Directive(60, "Prefer green tea.", "M", 0.7, 1, False, "global"),
    ]
    sel = select_for_query("please delete the database files", ds, k=2)
    assert any("database" in d.text for d in sel), sel
    fallback = select_for_query("", ds, k=2)
    assert len(fallback) == 2
    assert select_for_query("x", [], k=3) == []
    print("echo ok")


if __name__ == "__main__":
    _self_test()
