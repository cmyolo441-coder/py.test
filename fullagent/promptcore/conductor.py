"""conductor - one-object orchestrator.

    pc = Conductor()                     # reads systemprompt.USER_PROMPT
    wire = pc.weave(query=user_text)     # full prompt + attention layers
    notes = pc.mirror(reply)             # advisory reflections (or [])

Cached by prompt hash: a 49KB prompt is mapped/graded/distilled ONCE;
per-turn work is echo selection + anchor assembly (microseconds).
The prompt itself is never modified. No enforcement, no security
hooks, no extra model calls."""
from __future__ import annotations

import hashlib
import threading
from typing import Dict, List, Optional, Tuple

from .atlas import build_atlas
from .lexicon import Directive, parse_directives
from .distill import digest
from .echo import select_for_query
from .anchor import build_anchor
from .mirror import reflect
from .weave import compose_wire

_CACHE: Dict[str, Tuple] = {}
_CACHE_MAX = 4


class Conductor:
    """Stateless-per-prompt orchestrator. Thread-safe cache."""

    def __init__(self, prompt: Optional[str] = None,
                 digest_chars: int = 3500, anchor_chars: int = 2200,
                 turn_rules: int = 8):
        self._explicit = prompt
        self.digest_chars = digest_chars
        self.anchor_chars = anchor_chars
        self.turn_rules = turn_rules
        self._lock = threading.Lock()
        self.weaves = 0
        self.mirrors = 0
        self.notes_emitted = 0

    def _prompt(self) -> str:
        if self._explicit is not None:
            return self._explicit
        try:
            from .. import systemprompt as sp
            return sp.USER_PROMPT or ""
        except Exception:
            return ""

    def _layers(self, prompt: str) -> Tuple[List[Directive], str]:
        key = hashlib.sha256(prompt.encode("utf-8", "replace")).hexdigest()
        with self._lock:
            hit = _CACHE.get(key)
        if hit is not None:
            return hit
        atlas = build_atlas(prompt)
        directives = parse_directives(atlas)
        dig = digest(directives, max_chars=self.digest_chars)
        val = (directives, dig)
        with self._lock:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = val
        return val

    def weave(self, query: str = "") -> str:
        """FULL prompt verbatim + digest + turn anchor. Empty when no
        user prompt is set (caller keeps its built-in default)."""
        prompt = self._prompt()
        if not prompt:
            return ""
        directives, dig = self._layers(prompt)
        turn = select_for_query(query, directives, k=self.turn_rules)
        anchor = build_anchor(directives, turn, max_chars=self.anchor_chars)
        self.weaves += 1
        return compose_wire(prompt, dig, anchor)

    def mirror(self, reply: str) -> List[str]:
        """Advisory reflections; caller may ignore them entirely."""
        prompt = self._prompt()
        if not prompt or not reply:
            return []
        directives, _ = self._layers(prompt)
        notes = reflect(reply, directives)
        self.mirrors += 1
        self.notes_emitted += len(notes)
        return notes

    def stats(self) -> dict:
        prompt = self._prompt()
        n = 0
        if prompt:
            directives, _ = self._layers(prompt)
            n = len(directives)
        return {"prompt_chars": len(prompt), "directives": n,
                "weaves": self.weaves, "mirrors": self.mirrors,
                "notes_emitted": self.notes_emitted}


def _self_test() -> None:
    NL = chr(10)
    p = NL.join(["# Identity", "You are TestBot.", "## Rules",
                 "1. Always reply in Hinglish.", "- Never use emojis.",
                 "- Keep replies under 50 words.",
                 "- Verify files before editing them."])
    pc = Conductor(prompt=p)
    wire = pc.weave(query="edit my file please")
    assert wire.startswith(p)
    assert "Always reply in Hinglish." in wire
    assert "Active for this turn" in wire
    notes = pc.mirror("hello " + chr(0x1F600))
    assert any("emoji" in n for n in notes)
    assert pc.mirror("clean reply") == []
    s = pc.stats()
    assert s["directives"] >= 4 and s["weaves"] == 1
    empty = Conductor(prompt="")
    assert empty.weave() == "" and empty.mirror("x") == []
    print("conductor ok")


if __name__ == "__main__":
    _self_test()
