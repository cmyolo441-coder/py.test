"""mirror - soft post-reply reflection. ADVISORY notes only: which
mechanically visible directives the reply drifted from. Never blocks,
never rewrites, never retries. Zero model calls."""
from __future__ import annotations

import re
from typing import List

from .lexicon import Directive

BS = chr(92)
NL = chr(10)
Q = chr(34)
SQ = chr(39)
_QUOTED = re.compile("[" + Q + SQ + chr(8220) + chr(8221) + chr(96) + "]"
                     "([^" + Q + SQ + chr(8220) + chr(8221) + chr(96) + NL + "]{2,60})"
                     "[" + Q + SQ + chr(8220) + chr(8221) + chr(96) + "]")
_STARTS = re.compile(r"(start|begin|open)[a-z]*[^.]{0,40}?with", re.I)
_ENDS = re.compile(r"(end|close|finish)[a-z]*[^.]{0,40}?with", re.I)
_LIMIT = re.compile(r"(max(?:imum)?|at most|no more than|under|within|only|just)"
                    r"[^0-9]{0,12}([0-9]{1,5})[^a-z]{0,3}(words?|chars?|characters?|lines?|sentences?)", re.I)
_NOEMOJI = re.compile(r"(no|never|without|avoid)[^.]{0,20}emoji", re.I)
_DEVANAGARI = re.compile("[" + BS + "u0900-" + BS + "u097f]")
_HINGLISH = re.compile(r"(hinglish|roman hindi|latin script)", re.I)
_EMOJI = re.compile("[" + BS + "U0001F300-" + BS + "U0001FAFF" + BS + "U00002600-" + BS + "U000027BF]")
FENCE = chr(96) * 3
_CODE = re.compile(FENCE + ".*?(" + FENCE + "|$)", re.S)


def reflect(reply: str, directives: List[Directive]) -> List[str]:
    """Advisory reflections. Empty = nothing observed. Code excluded."""
    if not reply or not directives:
        return []
    text = _CODE.sub(" ", reply)
    notes: List[str] = []
    for d in directives:
        low = d.text.lower()
        m = _QUOTED.search(d.text)
        if m and _STARTS.search(low) and not text.lstrip().startswith(m.group(1)):
            notes.append("Your prompt asks replies to open with " + repr(m.group(1)) + ".")
        if m and _ENDS.search(low) and not text.rstrip().endswith(m.group(1)):
            notes.append("Your prompt asks replies to close with " + repr(m.group(1)) + ".")
        lim = _LIMIT.search(low)
        if lim and any(w in low for w in ("reply", "answer", "response", "output")):
            n = int(lim.group(2))
            unit = lim.group(3).rstrip("s")
            if unit == "word" and len(text.split()) > n:
                notes.append("Your prompt caps replies at " + str(n) + " words; this one is longer.")
            elif unit in ("char", "character") and len(text) > n:
                notes.append("Your prompt caps replies at " + str(n) + " characters; this one is longer.")
            elif unit == "line" and len([l for l in text.split(NL) if l.strip()]) > n:
                notes.append("Your prompt caps replies at " + str(n) + " lines; this one is longer.")
            elif unit == "sentence":
                cnt = len(re.findall("[.!?]+", text))
                if cnt > n:
                    notes.append("Your prompt caps replies at " + str(n) + " sentences; this one has more.")
        if _NOEMOJI.search(low) and _EMOJI.search(text):
            notes.append("Your prompt avoids emojis; this reply contains some.")
        if _HINGLISH.search(low) and _DEVANAGARI.search(text):
            notes.append("Your prompt asks for Hinglish/latin script; Devanagari appeared.")
    seen = set()
    out = []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _self_test() -> None:
    ds = [
        Directive(1, "Always start every reply with " + Q + "Jai:" + Q, "R", 0.1, 3, False, "reply"),
        Directive(2, "Never use emojis in replies.", "R", 0.2, 3, True, "reply"),
        Directive(3, "Keep replies under 10 words.", "R", 0.3, 3, False, "reply"),
        Directive(4, "Always reply in Hinglish only.", "R", 0.4, 3, False, "reply"),
    ]
    bad = "Namaste! " + chr(0x1F600) + " this reply is definitely much longer than ten words " + chr(0x0939) + "i"
    notes = reflect(bad, ds)
    assert len(notes) >= 3, notes
    good = "Jai: short hinglish reply"
    assert reflect(good, ds) == [], reflect(good, ds)
    assert reflect("", ds) == []
    assert reflect("anything", []) == []
    print("mirror ok")


if __name__ == "__main__":
    _self_test()
