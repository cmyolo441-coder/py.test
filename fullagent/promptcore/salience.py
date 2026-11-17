"""salience - attention-weight score per directive. Strength +
middle-sag compensation + scope + specificity. Pure arithmetic."""
from __future__ import annotations

import re
from typing import List, Tuple

from .lexicon import Directive, MUST, SHOULD, PREFER

BS = chr(92)
_NUM = re.compile(BS + "d")
_Q = chr(34)
_S = chr(39)
_QUOTED = re.compile(_Q + "[^" + _Q + "]+" + _Q + "|" + _S + "[^" + _S + "]+" + _S)


def middle_sag(depth: float) -> float:
    """0 at both ends, 1 at the exact middle."""
    d = min(max(depth, 0.0), 1.0)
    return 1.0 - abs(2.0 * d - 1.0)


def score(d: Directive) -> float:
    """Higher = more deserving of echo space. Range ~0..10."""
    s = {MUST: 4.0, SHOULD: 2.5, PREFER: 1.0}.get(d.strength, 0.0)
    s += 2.0 * middle_sag(d.depth)
    if d.scope == "reply":
        s += 1.5
    elif d.scope == "identity":
        s += 1.2
    elif d.scope == "workflow":
        s += 0.8
    if d.negative:
        s += 0.5
    if _NUM.search(d.text):
        s += 0.6
    if _QUOTED.search(d.text):
        s += 0.6
    return round(s, 3)


def ranked(directives: List[Directive]) -> List[Tuple[Directive, float]]:
    pairs = [(d, score(d)) for d in directives]
    pairs.sort(key=lambda p: -p[1])
    return pairs


def _self_test() -> None:
    mid = Directive(50, "Always verify the output format.", "R", 0.5, MUST, False, "reply")
    end = Directive(99, "Always verify the output format.", "R", 1.0, MUST, False, "reply")
    assert score(mid) > score(end)
    weak = Directive(10, "Prefer tea.", "R", 0.5, PREFER, False, "global")
    assert score(mid) > score(weak)
    r = ranked([weak, end, mid])
    assert r[0][0] is mid
    print("salience ok")


if __name__ == "__main__":
    _self_test()
