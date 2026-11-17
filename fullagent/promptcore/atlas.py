"""atlas - structural map of a long system prompt. Every line gets an
address (section path + line number + offset). Position is attention:
start/end rules are recalled far better than middle ones."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

BS = chr(92)
NL = chr(10)
FENCE = chr(96) * 3


def _rx(pat: str) -> str:
    return pat.replace("[@s]", BS + "s").replace("[@d]", BS + "d")


_HEADING = re.compile(_rx(r"^(#{1,6})[@s]+(.*)$"))
_NUM_ITEM = re.compile(_rx(r"^[@s]{0,3}([@d]{1,3})[.)][@s]+(.*)$"))
_BULLET = re.compile(_rx(r"^[@s]{0,3}[-*+•][@s]+(.*)$"))
_FENCE = re.compile(_rx(r"^[@s]{0,3}(") + FENCE + "|~~~)")
_SETEXT = re.compile(_rx(r"^[@s]{0,3}(={3,}|-{3,})[@s]*$"))


@dataclass(frozen=True)
class Line:
    n: int
    offset: int
    text: str
    section: str
    kind: str
    depth: float


@dataclass
class Atlas:
    prompt_hash: str
    total_chars: int
    total_lines: int
    lines: List[Line] = field(default_factory=list)
    sections: Dict[str, List[int]] = field(default_factory=dict)

    def region(self, lo: float, hi: float) -> List[Line]:
        return [l for l in self.lines if lo <= l.depth <= hi]

    def section_lines(self, name: str) -> List[Line]:
        return [self.lines[i] for i in self.sections.get(name, [])]


def build_atlas(prompt: str) -> Atlas:
    """Map the prompt. Never modifies it; metadata only."""
    raw = prompt or ""
    h = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]
    rows = raw.split(NL)
    n_rows = max(len(rows) - 1, 1)
    atlas = Atlas(prompt_hash=h, total_chars=len(raw), total_lines=len(rows))
    stack: List[Tuple[int, str]] = []
    offset = 0
    in_code = False
    prev_prose_idx = -1
    for i, text in enumerate(rows):
        depth = i / n_rows
        stripped = text.strip()
        kind = "prose"
        if _FENCE.match(text):
            in_code = not in_code
            kind = "code"
        elif in_code:
            kind = "code"
        elif not stripped:
            kind = "blank"
        else:
            m = _HEADING.match(text)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                kind = "heading"
            elif _SETEXT.match(text) and prev_prose_idx >= 0:
                prev = atlas.lines[prev_prose_idx]
                title = prev.text.strip()
                level = 1 if text.strip().startswith("=") else 2
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                atlas.lines[prev_prose_idx] = Line(
                    prev.n, prev.offset, prev.text,
                    " > ".join(t for _, t in stack[:-1]), "heading",
                    prev.depth)
                kind = "heading"
            elif _NUM_ITEM.match(text):
                kind = "item"
            elif _BULLET.match(text):
                kind = "bullet"
        section = " > ".join(t for _, t in stack)
        line = Line(i, offset, text, section, kind, depth)
        atlas.lines.append(line)
        atlas.sections.setdefault(section, []).append(i)
        if kind == "prose":
            prev_prose_idx = i
        offset += len(text) + 1
    return atlas


def _self_test() -> None:
    p = NL.join(["# Identity", "You are X.", "", "## Rules",
                 "1. Always be brief.", "- Never shout.", FENCE,
                 "Always inside code", FENCE, "Tail prose."])
    a = build_atlas(p)
    assert a.total_lines == 10
    kinds = [l.kind for l in a.lines]
    assert kinds[0] == "heading" and kinds[3] == "heading"
    assert kinds[4] == "item" and kinds[5] == "bullet"
    assert kinds[7] == "code", kinds
    assert a.lines[4].section == "Identity > Rules"
    assert a.region(0.9, 1.0)[-1].text == "Tail prose."
    b = build_atlas(NL.join(["Title", "=====", "body"]))
    assert b.lines[0].kind == "heading"
    print("atlas ok")


if __name__ == "__main__":
    _self_test()
