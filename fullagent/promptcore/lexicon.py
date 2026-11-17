"""lexicon - directive grammar. Grades each instruction: strength
(MUST/SHOULD/PREFER), polarity (do/never), scope (reply/tool/workflow/
identity/global). Descriptive, not enforcement - grades feed salience."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from .atlas import Atlas

BS = chr(92)


def _rx(pat: str) -> str:
    return (pat.replace("[@s]", BS + "s").replace("[@d]", BS + "d")
               .replace("[@b]", BS + "b"))


MUST, SHOULD, PREFER, INFO = 3, 2, 1, 0

_MUST = re.compile(_rx(
    r"[@b](always|must|never|do not|don'?t|dont|required|mandatory|"
    r"strictly|only|forbidden|prohibited|hamesha|kabhi nahi|mat|zaroor|"
    r"ensure|guarantee|no matter what|under any circumstances)[@b]"), re.I)
_SHOULD = re.compile(_rx(
    r"[@b](should|shall|need to|have to|make sure|verify|check that|"
    r"avoid)[@b]"), re.I)
_PREFER = re.compile(_rx(
    r"[@b](prefer|try to|ideally|when possible|may|can|consider|"
    r"recommended)[@b]"), re.I)
_NEG = re.compile(_rx(
    r"[@b](never|do not|don'?t|dont|avoid|without)[@b]"
    r"|kabhi nahi|[@b]mat[@b]|nahi"), re.I)
_SCOPE_REPLY = re.compile(_rx(
    r"[@b](reply|replies|respond|response|answer|output|say|speak|write|"
    r"tone|style|format|language|words|sentences|emoji|markdown|"
    r"bolo|likho|jawab)[@b]"), re.I)
_SCOPE_TOOL = re.compile(_rx(
    r"[@b](tool|command|shell|bash|execute|run|file|edit|read|"
    r"search|fetch|call)[@b]"), re.I)
_SCOPE_FLOW = re.compile(_rx(
    r"[@b](before|after|first|then|step|workflow|plan|verify|confirm|"
    r"wait|until|once|finally|pehle|phir|baad)[@b]"), re.I)
_SCOPE_ID = re.compile(_rx(
    r"[@b]you are[@b]|[@b]your (name|role|purpose|identity)[@b]"
    r"|[@b]act as[@b]|[@b]persona[@b]"), re.I)
_EXAMPLEISH = re.compile(_rx(r"^[@s]*(e[.]g[.]|example|for instance|such as)[@b]"), re.I)
_LEAD_MARK = re.compile(_rx(r"^[@s]{0,3}(?:[-*+•]|[@d]{1,3}[.)])[@s]+"))
_IMPERATIVE = re.compile(r"^[A-Z][a-z]+" + BS + "b")


@dataclass(frozen=True)
class Directive:
    line_n: int
    text: str
    section: str
    depth: float
    strength: int
    negative: bool
    scope: str

    @property
    def key(self) -> str:
        return self.text.lower()


def _clean(text: str) -> str:
    t = _LEAD_MARK.sub("", text.strip())
    return t.lstrip("#").strip()


def grade(text: str) -> int:
    if _MUST.search(text):
        return MUST
    if _SHOULD.search(text):
        return SHOULD
    if _PREFER.search(text):
        return PREFER
    if _IMPERATIVE.match(text) and len(text.split()) <= 24:
        return SHOULD
    return INFO


def scope_of(text: str) -> str:
    if _SCOPE_ID.search(text):
        return "identity"
    best, best_n = "global", 0
    for name, rx in (("reply", _SCOPE_REPLY), ("tool", _SCOPE_TOOL),
                     ("workflow", _SCOPE_FLOW)):
        n = len(rx.findall(text))
        if n > best_n:
            best, best_n = name, n
    return best


def parse_directives(atlas: Atlas, max_directives: int = 400) -> List[Directive]:
    """Graded directives from mapped lines (code skipped). Generous
    cap: a 49KB prompt can carry hundreds of rules; selection is per-turn."""
    out: List[Directive] = []
    seen = set()
    for line in atlas.lines:
        if line.kind in ("code", "blank", "heading"):
            continue
        t = _clean(line.text)
        if len(t) < 8 or _EXAMPLEISH.match(t):
            continue
        g = grade(t)
        if g == INFO:
            continue
        if len(t) > 220:
            cut = t.find(". ")
            t = t[:cut + 1] if 0 < cut <= 220 else t[:220].rstrip()
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(Directive(line.n, t, line.section, line.depth,
                             g, bool(_NEG.search(t)), scope_of(t)))
        if len(out) >= max_directives:
            break
    return out


def _self_test() -> None:
    from .atlas import build_atlas, NL
    p = NL.join(["# Id", "You are TestBot.", "## Rules",
                 "1. Always reply in Hinglish.", "- Never use emojis.",
                 "- Prefer short paragraphs.",
                 "- Verify files before editing them.",
                 "Some plain background sentence about the world."])
    ds = parse_directives(build_atlas(p))
    by = {d.text: d for d in ds}
    assert "Always reply in Hinglish." in by
    assert by["Always reply in Hinglish."].strength == MUST
    assert by["Never use emojis."].negative
    assert by["Prefer short paragraphs."].strength == PREFER
    assert by["Verify files before editing them."].scope in ("tool", "workflow")
    assert by["Always reply in Hinglish."].scope == "reply"
    print("lexicon ok")


if __name__ == "__main__":
    _self_test()
